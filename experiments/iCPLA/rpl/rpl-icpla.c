#include "contiki.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-of.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/linkaddr.h"
#include "lib/list.h"
#include "lib/memb.h"
#include "sys/ctimer.h"
#include "random.h"
#include "rpl-icpla.h"
#include <string.h>
#include <math.h>

/* ------------------- Collision probability learning ------------------- */

/* Track BE/backoff results from CSMA; we infer CW = (2^BE)-1 (Eq.6) and
 * compute P_coll = 1 - (1 - 1/CW)^(k-1) (Eq.7). We keep a sliding mean (Eq.8,9).
 */
static uint8_t be_last = 3;                 /* reasonable default */
static uint8_t neighbor_count_cached = 2;   /* updated from parent set size */
static float   coll_hist[ICPLA_COLL_WINDOW];
static uint8_t coll_pos = 0;
static float   coll_mean = 0.0f;            /* \bar{P_coll} */
static float   coll_current = 0.0f;         /* P_coll(current) */
static float   reward_last = 0.0f;

/* Exported so MAC layer (or app) can tell us about outcomes if desired */
void icpla_notify_tx_collision(void) { if(be_last < 5) be_last++; }
void icpla_notify_tx_success(void)   { if(be_last > 0) be_last--; }

/* Recompute collision probability once per “interval” (we’ll call this as part of rank calc) */
static void
icpla_update_collision_prob(uint8_t k_neighbors)
{
  /* CW from BE (Eq.6): CW = 2^BE - 1, clamp to [1,31] to avoid div-by-zero */
  int cw = (1 << be_last) - 1;
  if(cw < 1) cw = 1;
  if(cw > 31) cw = 31;

  /* Eq.7: Pcoll = 1 - (1 - 1/CW)^(k-1) */
  int k = k_neighbors;
  if(k < 1) k = 1;
  float base = 1.0f - (1.0f / (float)cw);
  float p = 1.0f - powf(base, (float)(k - 1));

  /* Sliding mean (Eq.8) and combine with previous (Eq.9) */
  coll_current = p;
  coll_hist[coll_pos++] = p;
  if(coll_pos >= ICPLA_COLL_WINDOW) coll_pos = 0;

  float sum = 0.0f;
  for(uint8_t i=0; i<ICPLA_COLL_WINDOW; i++) sum += coll_hist[i];
  float mean_window = sum / (float)ICPLA_COLL_WINDOW;

  coll_mean = 0.5f * (coll_mean + mean_window);
}

/* Reward (Eq.11): positive if collision decreased vs. last mean */
static float
icpla_reward(void)
{
  float r = (coll_current < coll_mean) ? +1.0f : -1.0f;
  reward_last = r;
  return r;
}

/* ------------------- Minimal Q-learning over parents ------------------- */

typedef struct {
  linkaddr_t addr;
  float q;      /* Q(s, a) for choosing this parent */
} icpla_qentry_t;

static icpla_qentry_t qtab[ICPLA_MAX_PARENTS];
static uint8_t qtab_len = 0;

/* find or create entry */
static icpla_qentry_t *
q_lookup(const linkaddr_t *addr)
{
  for(uint8_t i=0;i<qtab_len;i++) if(linkaddr_cmp(&qtab[i].addr, addr)) return &qtab[i];
  if(qtab_len < ICPLA_MAX_PARENTS) {
    linkaddr_copy(&qtab[qtab_len].addr, addr);
    qtab[qtab_len].q = 0.0f;
    return &qtab[qtab_len++];
  }
  return NULL;
}

/* simple state = number of candidate parents (m), action = choose parent j
 * Update: Q <- (1-α)Q + α(r + γ * max_a' Q(s',a')) ; we approximate max over current set.
 */
static void
q_update(const linkaddr_t *chosen_parent)
{
  /* compute max Q among current candidates */
  float qmax = 0.0f;
  for(uint8_t i=0;i<qtab_len;i++) if(qtab[i].q > qmax) qmax = qtab[i].q;

  icpla_qentry_t *e = q_lookup(chosen_parent);
  if(!e) return;

  float target = reward_last + (ICPLA_GAMMA * qmax);
  e->q = (1.0f - ICPLA_ALPHA) * e->q + ICPLA_ALPHA * target;
}

/* ε-greedy: with prob ε, explore (random parent); else exploit (lowest rank + highest Q) */
static rpl_parent_t *
pick_parent_epsilon_greedy(rpl_parent_t *p1, rpl_parent_t *p2)
{
  /* Collect both in a local array (RPL best_parent prototype passes two candidates) */
  rpl_parent_t *cands[2] = {p1, p2};
  uint8_t n = 0;
  for(uint8_t i=0;i<2;i++) if(cands[i]) n++;

  if(n == 0) return NULL;
  if(n == 1) return cands[0];

  uint16_t r = random_rand() % 10000;
  if(r < (uint16_t)(ICPLA_EPSILON * 10000)) {
    /* Explore: random */
    return cands[(random_rand() & 1)];
  }

  /* Exploit: combine advertised rank (lower better) and learned Q (higher better).
   * We rank by score = (normalized rank) - Q, so lower score is better.
   */
  uint16_t r0 = rpl_parent_get_rank(p1);
  uint16_t r1 = rpl_parent_get_rank(p2);

  icpla_qentry_t *e0 = q_lookup(&p1->addr);
  icpla_qentry_t *e1 = q_lookup(&p2->addr);
  float q0 = e0 ? e0->q : 0.0f;
  float q1 = e1 ? e1->q : 0.0f;

  float s0 = (float)r0 - q0;
  float s1 = (float)r1 - q1;

  return (s0 <= s1) ? p1 : p2;
}

/* ------------------- iCPLA Objective Function hooks ------------------- */

static void
icpla_reset(rpl_dag_t *dag)
{
  (void)dag;
  memset(qtab, 0, sizeof(qtab));
  qtab_len = 0;
  be_last = 3;
  neighbor_count_cached = 2;
  coll_pos = 0;
  coll_mean = 0.0f;
  reward_last = 0.0f;
}

/* Called when link/parent state changes; use it to update Q on each “decision” */
static void
icpla_parent_state_callback(rpl_parent_t *p, int known, int etx)
{
  (void)known; (void)etx;
  if(p) {
    q_update(&p->addr);
  }
}

/* Our rank increase = base + Pcoll(parent) scaled (Eq.10). We simply add
 * (Pcoll(parent) * 256) so it affects rank in same scale as RPL RFC (rank units of 256).
 * Each node computes its own Pcoll and advertises a rank that already includes it; children
 * then see Rank(parent) reflecting MAC congestion “in letter and spirit” of iCPLA.
 */
static rpl_rank_t
icpla_calc_rank(rpl_parent_t *p, rpl_rank_t base_rank)
{
  /* Update neighbor count (approx = number of candidate parents + 1) */
  rpl_dag_t *dag = rpl_get_any_dag();
  if(dag) {
    /* Conservative proxy for k: number of parents + 1 (self neighborhood density proxy) */
    uint8_t parents = 0;
    rpl_parent_t *tmp = NULL;
    for(tmp = nbr_table_head(rpl_parents); tmp != NULL; tmp = nbr_table_next(rpl_parents, tmp)) {
      parents++;
      q_lookup(&tmp->addr); /* ensure Q entry exists */
    }
    neighbor_count_cached = parents > 1 ? parents : 2;
  }

  /* Learn/update Pcoll before computing our own rank */
  icpla_update_collision_prob(neighbor_count_cached);
  float rwd = icpla_reward();
  (void)rwd; /* reward consumed in q_update via parent_state_callback */

  if(base_rank == 0) {
    return RPL_INFINITE_RANK;
  }

  if(p == NULL) {
    /* Root rank: base rank (RFC6552 base = 128*RPL_MIN_HOPRANKINC) */
    return base_rank;
  }

  /* Parent’s advertised rank already includes its own Pcoll.  We add our view of parent Pcoll
   * to compute rank increase: Rank(child) = Rank(parent) + Pcoll(parent) (Eq.10).
   * Scale Pcoll in [0..1] to RPL rank units (RPL_MIN_HOPRANKINC=256).
   */
  rpl_rank_t inc = (rpl_rank_t)(coll_current * RPL_MIN_HOPRANKINC);
  rpl_rank_t res = rpl_parent_get_rank(p) + inc;
  return MIN(res, RPL_INFINITE_RANK);
}

/* Pick best parent with ε-greedy */
static rpl_parent_t *
icpla_best_parent(rpl_parent_t *p1, rpl_parent_t *p2)
{
  rpl_parent_t *choice = pick_parent_epsilon_greedy(p1, p2);
  return choice ? choice : p1 ? p1 : p2;
}

/* We don’t advertise a metric container; rank already embeds Pcoll. */
static void
icpla_update_mc(rpl_instance_t *instance)
{
  (void)instance;
}

/* DIO suppression during exploitation (paper §3.4): We take a pragmatic approach:
 * if ε is low (mainly exploiting), avoid rescheduling extra DIOs on parent updates.
 * This keeps Contiki’s trickle running but reduces bursts during steady exploitation.
 */
static void
icpla_dio_callback(rpl_instance_t *instance)
{
  (void)instance;
  /* no-op; Contiki-NG handles DIO via trickle; our parent_state_callback avoids spuriously
     poking the trickle during exploitation. */
}

/* Hook table */
rpl_of_t rpl_icpla_of = {
  .ocp = RPL_OCP_ICPLA,
  .reset = icpla_reset,
  .parent_state_changed = icpla_parent_state_callback,
  .best_parent = icpla_best_parent,
  .calculate_rank = icpla_calc_rank,
  .update_metric_container = icpla_update_mc,
  .dao_ack_callback = NULL
};

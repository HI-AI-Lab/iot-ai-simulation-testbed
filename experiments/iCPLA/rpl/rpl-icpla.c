#include "contiki.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-nbr.h"
#include "net/routing/rpl-lite/rpl-private.h"
#include "net/routing/rpl-lite/rpl-ext-header.h"
#include "net/routing/rpl-lite/rpl-timers.h"

#include "rpl-icpla.h"

/* ---------- Local helpers ---------- */

/* Weak default: if the application does not provide QLR, use 0 */
__attribute__((weak))
uint16_t icpla_get_local_qlr_fp(void) {
  return 0;
}

/* Return the link ETX (already scaled by RPL_ETX_DIVISOR) */
static uint16_t
link_etx(const rpl_parent_t *p)
{
  /* In RPL Lite, parent->link_metric holds ETX in fixed-point */
  return rpl_parent_get_link_metric((rpl_parent_t *)p);
}

/* Compute iCPLA per-link cost: ETX + α * QLR_sender (all fixed-point) */
static uint16_t
icpla_link_cost_fp(const rpl_parent_t *p)
{
  uint16_t etx_fp = link_etx(p);
  uint16_t qlr_fp = icpla_get_local_qlr_fp(); /* app-provided */
  /* cost_fp = etx_fp + (α * qlr_fp) / RPL_ETX_DIVISOR */
  uint32_t mix = (uint32_t)ICPLA_ALPHA_FP * (uint32_t)qlr_fp;
  uint16_t mix_scaled = (uint16_t)((mix + (RPL_ETX_DIVISOR / 2)) / RPL_ETX_DIVISOR);
  return (uint16_t)(etx_fp + mix_scaled);
}

/* Path cost through a parent (rank offset + link cost).
 * We follow MRHOF logic: path_cost = parent->rank + iCPLA link cost.
 * All values are in rank units (RPL_MIN_HOPRANKINC is the base step).
 */
static rpl_rank_t
icpla_path_cost_through(const rpl_parent_t *p)
{
  rpl_rank_t parent_rank = rpl_parent_get_rank((rpl_parent_t *)p);
  if(parent_rank == RPL_INFINITE_RANK) {
    return RPL_INFINITE_RANK;
  }

  /* Scale ETX-like units to rank units:
   * MRHOF adds ETX directly in "rank units"; in RPL Lite,
   * ETX and rank step share the same fixed-point convention (multiples of DIVISOR),
   * so adding them is consistent.
   */
  rpl_rank_t link_cost = (rpl_rank_t)icpla_link_cost_fp(p);

  /* Ensure we add at least one hop worth to avoid zero increments */
  rpl_rank_t base_step = (rpl_rank_t)RPL_MIN_HOPRANKINC;

  /* Total path cost */
  return parent_rank + base_step + link_cost;
}

/* ---------- rpl_of_t API ---------- */

static rpl_parent_t *
best_parent_icpla(rpl_parent_t *p1, rpl_parent_t *p2)
{
  if(p1 == NULL) return p2;
  if(p2 == NULL) return p1;

  rpl_rank_t c1 = icpla_path_cost_through(p1);
  rpl_rank_t c2 = icpla_path_cost_through(p2);

  if(c1 < c2) return p1;
  if(c2 < c1) return p2;

  /* Tie-breaker: prefer lower ETX, then lower rank, then lexicographic */
  uint16_t e1 = link_etx(p1);
  uint16_t e2 = link_etx(p2);
  if(e1 < e2) return p1;
  if(e2 < e1) return p2;

  rpl_rank_t r1 = rpl_parent_get_rank(p1);
  rpl_rank_t r2 = rpl_parent_get_rank(p2);
  if(r1 < r2) return p1;
  if(r2 < r1) return p2;

  return p1;
}

static rpl_rank_t
calculate_rank_icpla(rpl_parent_t *p, rpl_rank_t base_rank)
{
  if(p == NULL) {
    /* No parent: keep base rank (or infinite if invalid) */
    return base_rank == 0 ? RPL_INFINITE_RANK : base_rank;
  }

  rpl_rank_t path_cost = icpla_path_cost_through(p);
  if(path_cost < RPL_MIN_HOPRANKINC) {
    /* Sanity */
    path_cost = RPL_MIN_HOPRANKINC;
  }
  return path_cost;
}

static void
parent_state_changed_icpla(rpl_parent_t *p, rpl_parent_state_t state)
{
  (void)p; (void)state;
  /* No special hysteresis/state handling beyond RPL core. */
}

static void
update_mc_icpla(rpl_instance_t *instance)
{
  /* Advertise ETX as metric container, same as MRHOF */
  instance->mc.type = RPL_DAG_MC_ETX;
  instance->mc.flags = 0;
  instance->mc.aggr  = RPL_DAG_MC_AGGR_ADDITIVE;
  instance->mc.prec  = 0;
  instance->mc.length = sizeof(uint16_t);
  /* The DAG MC 'etx' field is in ETX fixed-point. Use our own preferred parent path cost. */
  rpl_parent_t *pr = nbr_table_head(rpl_parent_t, instance->parents);
  uint16_t min_path = 0xFFFF;
  while(pr != NULL) {
    rpl_rank_t c = icpla_path_cost_through(pr);
    if(c < min_path) min_path = (uint16_t)c;
    pr = nbr_table_next(rpl_parent_t, instance->parents, pr);
  }
  if(min_path == 0xFFFF) min_path = RPL_INFINITE_RANK;
  instance->mc.obj.etx = min_path;
}

static uint16_t
parent_link_metric_icpla(rpl_parent_t *p)
{
  /* For DAO path selection decisions where OF is asked for link metric only */
  return icpla_link_cost_fp(p);
}

/* The OF vtable */
rpl_of_t rpl_icpla = {
  .ocp = 0xFE01,                     /* Unofficial OCP to avoid colliding with standardized ones */
  .best_parent = best_parent_icpla,
  .calculate_rank = calculate_rank_icpla,
  .parent_state_callback = parent_state_changed_icpla,
  .update_metric_container = update_mc_icpla,
  .parent_link_metric = parent_link_metric_icpla
};

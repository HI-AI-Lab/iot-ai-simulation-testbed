#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "sys/energest.h"
#include "sys/log.h"
#include "random.h"
#include "sys/node-id.h"
#include "clock.h"
#include "sys/rtimer.h"
#include "net/link-stats.h"

#include "rpl-icpla.h" /* icpla_alpha_fp + icpla_current_qlr_fp() */

#include <stdbool.h>
#include <stdint.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ----------------------- Config ----------------------- */
#define UDP_PORT              8765
#define SEND_INTERVAL_SEC     APP_CONF_SEND_INTERVAL_SEC
#define PAYLOAD_SIZE          APP_CONF_PAYLOAD_SIZE
#define DROP_PROB_PER_TEN     APP_CONF_DROP_PROB_PER_TEN

/* Energy model */
#define V_SUPPLY_V            APP_CONF_V_SUPPLY_V
#define I_CPU_mA              APP_CONF_I_CPU_mA
#define I_TX_mA               APP_CONF_I_TX_mA
#define I_RX_mA               APP_CONF_I_RX_mA
#define ENERGY_BUDGET_mJ      APP_CONF_ENERGY_BUDGET_mJ

#ifndef LINK_STATS_ETX_DIVISOR
#define LINK_STATS_ETX_DIVISOR 128
#endif

/* -------------------- State --------------------------- */
static struct simple_udp_connection udp_conn;
static struct etimer periodic_timer;

static uint32_t generated = 0, received = 0, dropped = 0;
static float total_energy_mJ = 0.0f;
static bool is_dead = false;

static unsigned long last_cpu = 0, last_tx = 0, last_rx = 0;

/* Per-packet info for PRR/E2E */
static uint32_t seqno = 0;
typedef struct {
  uint16_t src_id;
  uint32_t seq;
  uint32_t tx_ticks; /* RTIMER_NOW() at send time */
  uint8_t  pad[PAYLOAD_SIZE - 10];
} __attribute__((packed)) pkt_t;

/* ---------------- iCPLA: QLR provider (fixed-point /128) -------- */
static uint16_t icpla_qlr_fp = 0; /* smoothed QLR */

#define ICPLA_Q_SMOOTH_NUM 9
#define ICPLA_Q_SMOOTH_DEN 10

static void
icpla_update_qlr_fp(void)
{
  float q = (generated == 0) ? 0.0f : ((float)dropped / (float)generated);
  uint32_t inst_fp = (uint32_t)(q * LINK_STATS_ETX_DIVISOR + 0.5f);
  icpla_qlr_fp = (uint16_t)(
      (ICPLA_Q_SMOOTH_NUM * (uint32_t)icpla_qlr_fp +
      (ICPLA_Q_SMOOTH_DEN - ICPLA_Q_SMOOTH_NUM) * inst_fp) / ICPLA_Q_SMOOTH_DEN);
}

/* Exported to OF */
uint16_t icpla_current_qlr_fp(void) { return icpla_qlr_fp; }

/* ---------------- RL to tune alpha (on-node Q-learning) --------- */
/* State: discretized QLR (4 bins). Action: α ∈ {0,8,16,24,32} (/128). */
#define RL_NUM_STATES   4
#define RL_NUM_ACTIONS  5
#define RL_INTERVAL_SEC 10

static float     Q[RL_NUM_STATES][RL_NUM_ACTIONS];
static int       s_prev = -1, a_prev = -1;
static struct etimer rl_timer;

/* Hyperparams */
#define RL_EPS_START  0.20f
#define RL_EPS_MIN    0.02f
#define RL_EPS_DECAY  0.995f
#define RL_ALPHA_LR   0.10f
#define RL_GAMMA      0.80f

static float epsilon = RL_EPS_START;
static const uint16_t ACTION_ALPHA_FP[RL_NUM_ACTIONS] = { 0, 8, 16, 24, 32 };

static int rl_state_from_qlr(uint16_t qlr_fp)
{
  if(qlr_fp < (uint16_t)(0.05f * LINK_STATS_ETX_DIVISOR)) return 0;
  if(qlr_fp < (uint16_t)(0.15f * LINK_STATS_ETX_DIVISOR)) return 1;
  if(qlr_fp < (uint16_t)(0.30f * LINK_STATS_ETX_DIVISOR)) return 2;
  return 3;
}

static int rl_choose_action(int s)
{
  if(((float)random_rand() / (float)RANDOM_RAND_MAX) < epsilon) {
    return random_rand() % RL_NUM_ACTIONS;
  }
  int best_a = 0;
  float best_q = Q[s][0];
  for(int a = 1; a < RL_NUM_ACTIONS; a++) {
    if(Q[s][a] > best_q) { best_q = Q[s][a]; best_a = a; }
  }
  return best_a;
}

static float rl_reward_from_qlr(uint16_t qlr_fp)
{
  float q = (float)qlr_fp / (float)LINK_STATS_ETX_DIVISOR;
  float r = 1.0f - q; /* lower QLR -> higher reward */
  if(r < 0) r = 0; if(r > 1) r = 1;
  return r;
}

static void rl_step_update(int s_curr, float reward)
{
  if(s_prev < 0 || a_prev < 0) return;

  float max_next = Q[s_curr][0];
  for(int a = 1; a < RL_NUM_ACTIONS; a++) if(Q[s_curr][a] > max_next) max_next = Q[s_curr][a];

  float *Qsa = &Q[s_prev][a_prev];
  *Qsa = (1.0f - RL_ALPHA_LR) * (*Qsa) + RL_ALPHA_LR * (reward + RL_GAMMA * max_next);
}

static void rl_apply_action(int a)
{
  icpla_alpha_fp = ACTION_ALPHA_FP[a]; /* visible to OF */
  LOG_INFO("RL_ALPHA node=%u a=%d alpha_fp=%u (%.3f)\n",
           node_id, a, icpla_alpha_fp, (float)icpla_alpha_fp / (float)LINK_STATS_ETX_DIVISOR);
}

/* ---------------- Energy + Logging -------------------- */
static void
update_energy_and_maybe_die(void)
{
  energest_flush();

  unsigned long cpu_now = energest_type_time(ENERGEST_TYPE_CPU);
  unsigned long tx_now  = energest_type_time(ENERGEST_TYPE_TRANSMIT);
  unsigned long rx_now  = energest_type_time(ENERGEST_TYPE_LISTEN);

  unsigned long cpu_diff = cpu_now - last_cpu;
  unsigned long tx_diff  = tx_now  - last_tx;
  unsigned long rx_diff  = rx_now  - last_rx;

  last_cpu = cpu_now; last_tx = tx_now; last_rx = rx_now;

  const float TICKS_PER_SEC = (float)ENERGEST_SECOND;
  float cpu_s = cpu_diff / TICKS_PER_SEC;
  float tx_s  = tx_diff  / TICKS_PER_SEC;
  float rx_s  = rx_diff  / TICKS_PER_SEC;

  float energy_period_mJ = V_SUPPLY_V * (
    (I_CPU_mA/1000.0f) * cpu_s +
    (I_TX_mA /1000.0f) * tx_s  +
    (I_RX_mA /1000.0f) * rx_s
  ) * 1000.0f;

  total_energy_mJ += energy_period_mJ;

  if(!is_dead && total_energy_mJ >= ENERGY_BUDGET_mJ) {
    is_dead = true;
    LOG_INFO("DEAD %u %.1f\n", node_id, total_energy_mJ);
  }
}

static void
report_qlr(void)
{
  float qlr = (generated == 0) ? 0.0f : ((float)dropped / (float)generated);
  LOG_INFO("QLR %u %lu %lu %lu %.3f\n",
           node_id, (unsigned long)generated, (unsigned long)received,
           (unsigned long)dropped, qlr);
}

/* ---------------- UDP receive callback ---------------- */
static void
recv_cb(struct simple_udp_connection *c,
        const uip_ipaddr_t *sender_addr,
        uint16_t sender_port,
        const uip_ipaddr_t *receiver_addr,
        uint16_t receiver_port,
        const uint8_t *data,
        uint16_t datalen)
{
  (void)c; (void)sender_port; (void)receiver_port; (void)receiver_addr;

  received++;

  /* At the sink, compute E2E one line per packet */
  if(NETSTACK_ROUTING.node_is_root() && datalen >= sizeof(pkt_t)) {
    const pkt_t *pp = (const pkt_t *)data;
    rtimer_clock_t now = RTIMER_NOW();
    uint32_t e2e_ms = (uint32_t)((now - pp->tx_ticks) * 1000ULL / RTIMER_SECOND);
    LOG_INFO("E2E src=%u seq=%lu e2e_ms=%lu\n",
             (unsigned)pp->src_id, (unsigned long)pp->seq, (unsigned long)e2e_ms);
  }

  LOG_INFO("RECV %u %u from ", node_id, datalen);
  LOG_INFO_6ADDR(sender_addr);
  LOG_INFO_("\n");
}

/* ---------------- Process ----------------------------- */
PROCESS(app_process, "UDP sender/receiver + iCPLA+RL");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static pkt_t p;
  static uip_ipaddr_t dest;

  PROCESS_BEGIN();

  /* Root setup on node 1 */
  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
    LOG_INFO("ROOT STARTED (node 1)\n");
  }

  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, recv_cb);

  for(size_t i = 0; i < sizeof(p.pad); i++) p.pad[i] = (uint8_t)(i & 0xFF);

  etimer_set(&periodic_timer, CLOCK_SECOND * SEND_INTERVAL_SEC);
  etimer_set(&rl_timer, CLOCK_SECOND * RL_INTERVAL_SEC);

  /* Initialize alpha for safety */
  icpla_alpha_fp = ICPLA_ALPHA_FP_DEFAULT;

  while(1) {
    PROCESS_WAIT_EVENT();

    if(etimer_expired(&periodic_timer)) {
      etimer_reset(&periodic_timer);

      update_energy_and_maybe_die();
      icpla_update_qlr_fp(); /* keep QLR fresh */

      if(!is_dead &&
         NETSTACK_ROUTING.node_is_reachable() &&
         NETSTACK_ROUTING.get_root_ipaddr(&dest)) {

        /* Local queue drop model to drive QLR signal */
        int r = random_rand() % 10; /* 0..9 */
        if(r < DROP_PROB_PER_TEN) {
          dropped++;
          LOG_INFO("DROP %u\n", node_id);
        } else {
          p.src_id   = (uint16_t)node_id;
          p.seq      = ++seqno;
          p.tx_ticks = RTIMER_NOW();

          simple_udp_sendto(&udp_conn, &p, sizeof(p), &dest);
          generated++;
          LOG_INFO("SEND %u %u seq=%lu\n",
                   node_id, (unsigned)sizeof(p), (unsigned long)p.seq);
        }
      } else if(!NETSTACK_ROUTING.node_is_reachable()) {
        LOG_INFO("NO_ROUTE %u\n", node_id);
      }

      report_qlr();
    }

    if(etimer_expired(&rl_timer)) {
      etimer_reset(&rl_timer);

      int   s_curr = rl_state_from_qlr(icpla_qlr_fp);
      float reward = rl_reward_from_qlr(icpla_qlr_fp);

      rl_step_update(s_curr, reward);

      int a_curr = rl_choose_action(s_curr);
      rl_apply_action(a_curr);

      s_prev = s_curr;
      a_prev = a_curr;

      epsilon = (epsilon > RL_EPS_MIN) ? (epsilon * RL_EPS_DECAY) : RL_EPS_MIN;

      LOG_INFO("RL_STEP node=%u s=%d a=%d r=%.3f eps=%.3f qlr_fp=%u alpha_fp=%u\n",
               node_id, s_curr, a_curr, reward, epsilon, icpla_qlr_fp, icpla_alpha_fp);
    }
  }

  PROCESS_END();
}

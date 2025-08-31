#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "sys/energest.h"
#include "sys/log.h"
#include "random.h"
#include "sys/node-id.h"
#include "sys/clock.h"

#include <stdio.h>
#include <inttypes.h>
#include <stdbool.h>
#include <string.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

#ifndef APP_SEND_INTERVAL_SEC
#define APP_SEND_INTERVAL_SEC 5
#endif
#ifndef APP_STATS_INTERVAL_SEC
#define APP_STATS_INTERVAL_SEC 10
#endif
#ifndef APP_UDP_DATA_PORT
#define APP_UDP_DATA_PORT 8765
#endif
#ifndef APP_UDP_ACK_PORT
#define APP_UDP_ACK_PORT 8766
#endif
#ifndef APP_MAX_OUTSTANDING_ACKS
#define APP_MAX_OUTSTANDING_ACKS 5
#endif

#ifndef APP_VOLTAGE_V
#define APP_VOLTAGE_V (3.0f)
#endif
#ifndef APP_I_CPU_MA
#define APP_I_CPU_MA (1.8f)
#endif
#ifndef APP_I_LPM_MA
#define APP_I_LPM_MA (0.054f)
#endif
#ifndef APP_I_TX_MA
#define APP_I_TX_MA (17.4f)
#endif
#ifndef APP_I_RX_MA
#define APP_I_RX_MA (18.8f)
#endif
#ifndef APP_ENERGY_BUDGET_MJ
#define APP_ENERGY_BUDGET_MJ (18000.0f)
#endif

typedef struct __attribute__((packed)) {
  uint8_t  src_id;
  uint16_t seq;
  uint32_t t_ms;
} data_msg_t;

typedef struct __attribute__((packed)) {
  uint8_t  src_id;
  uint16_t seq;
} ack_msg_t;

static struct simple_udp_connection udp_data;
static struct simple_udp_connection udp_ack;

typedef struct {
  uint16_t seq;
  uint32_t sent;
  uint32_t acked;
  uint32_t dropped;
  uint16_t last_unacked_seq;
  uint8_t  outstanding_acks;
  float    prr_local_last;
} app_tx_t;

typedef struct {
  uint16_t last_seq[256];
  uint32_t recv_total;
  uint32_t expected_total;
  uint16_t sources_active;
} root_rx_t;

static app_tx_t TX;
static root_rx_t RX;

static float energy_remaining_mJ = APP_ENERGY_BUDGET_MJ;
static uint8_t dead_logged = 0;

static uint32_t last_cpu = 0, last_lpm = 0, last_tx = 0, last_rx = 0;

static inline uint32_t now_ms(void) {
  return (uint32_t)((uint64_t)clock_time() * 1000ull / CLOCK_SECOND);
}

static float energest_period_mJ(void) {
  energest_flush();
  uint32_t cpu = ENERGEST_GET_TOTAL(ENERGEST_TYPE_CPU);
  uint32_t lpm = ENERGEST_GET_TOTAL(ENERGEST_TYPE_LPM);
  uint32_t tx  = ENERGEST_GET_TOTAL(ENERGEST_TYPE_TRANSMIT);
  uint32_t rx  = ENERGEST_GET_TOTAL(ENERGEST_TYPE_LISTEN);

  uint32_t d_cpu = cpu - last_cpu;
  uint32_t d_lpm = lpm - last_lpm;
  uint32_t d_tx  = tx  - last_tx;
  uint32_t d_rx  = rx  - last_rx;

  last_cpu = cpu; last_lpm = lpm; last_tx = tx; last_rx = rx;

  const float sec_cpu = (float)d_cpu / (float)ENERGEST_SECOND;
  const float sec_lpm = (float)d_lpm / (float)ENERGEST_SECOND;
  const float sec_tx  = (float)d_tx  / (float)ENERGEST_SECOND;
  const float sec_rx  = (float)d_rx  / (float)ENERGEST_SECOND;

  float mJ = 0.0f;
  mJ += APP_VOLTAGE_V * APP_I_CPU_MA * sec_cpu;
  mJ += APP_VOLTAGE_V * APP_I_LPM_MA * sec_lpm;
  mJ += APP_VOLTAGE_V * APP_I_TX_MA  * sec_tx;
  mJ += APP_VOLTAGE_V * APP_I_RX_MA  * sec_rx;
  return mJ;
}

static void battery_tick_and_maybe_log_dead(void) {
  if(dead_logged) return;
  energy_remaining_mJ -= energest_period_mJ();
  if(energy_remaining_mJ <= 0.0f) {
    energy_remaining_mJ = 0.0f;
    printf("METRIC NLT DEAD node=%u t_ms=%" PRIu32 " energy_mJ=0\n",
           node_id, now_ms());
    dead_logged = 1;
  }
}

static void
data_rx_cb(struct simple_udp_connection *c,
           const uip_ipaddr_t *sender_addr,
           uint16_t sender_port,
           const uip_ipaddr_t *receiver_addr,
           uint16_t receiver_port,
           const uint8_t *data,
           uint16_t datalen)
{
  (void)c; (void)sender_port; (void)receiver_port; (void)receiver_addr;
  battery_tick_and_maybe_log_dead();
  if(datalen < (int)sizeof(data_msg_t)) return;

  const data_msg_t *m = (const data_msg_t *)data;
  const uint32_t e2e = now_ms() - m->t_ms;
  printf("METRIC E2E src=%u seq=%u e2e_ms=%" PRIu32 "\n",
         (unsigned)m->src_id, (unsigned)m->seq, e2e);

  uint8_t sid = m->src_id;
  if(RX.last_seq[sid] == 0) {
    RX.expected_total += 1;
    RX.sources_active += 1;
  } else {
    const uint16_t prev = RX.last_seq[sid];
    if(m->seq > prev) RX.expected_total += (uint32_t)(m->seq - prev);
    else RX.expected_total += 1;
  }
  RX.recv_total += 1;
  RX.last_seq[sid] = m->seq;

  ack_msg_t ack = (ack_msg_t){ .src_id = m->src_id, .seq = m->seq };
  simple_udp_sendto(&udp_ack, &ack, sizeof(ack), sender_addr);
}

static void
ack_rx_cb(struct simple_udp_connection *c,
          const uip_ipaddr_t *sender_addr,
          uint16_t sender_port,
          const uip_ipaddr_t *receiver_addr,
          uint16_t receiver_port,
          const uint8_t *data,
          uint16_t datalen)
{
  (void)c; (void)sender_addr; (void)sender_port; (void)receiver_addr; (void)receiver_port;
  battery_tick_and_maybe_log_dead();
  if(datalen < (int)sizeof(ack_msg_t)) return;

  const ack_msg_t *ack = (const ack_msg_t *)data;
  if(ack->src_id == (uint8_t)node_id) {
    TX.acked++;
    if(TX.outstanding_acks > 0) TX.outstanding_acks--;
  }
}

PROCESS(app_process, "OF0 UDP app (metrics: NLT/QLR/PRR/E2E)");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static struct etimer send_et;
  static struct etimer stats_et;

  PROCESS_BEGIN();

  memset(&TX, 0, sizeof(TX));
  memset(&RX, 0, sizeof(RX));

  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
    LOG_INFO("ROOT STARTED (node 1)\n");
  }

  simple_udp_register(&udp_data, APP_UDP_DATA_PORT, NULL, APP_UDP_DATA_PORT, data_rx_cb);
  simple_udp_register(&udp_ack,  APP_UDP_ACK_PORT,  NULL, APP_UDP_ACK_PORT,  ack_rx_cb);

  etimer_set(&send_et,  CLOCK_SECOND * APP_SEND_INTERVAL_SEC);
  etimer_set(&stats_et, CLOCK_SECOND * APP_STATS_INTERVAL_SEC);

  uip_ipaddr_t dest;
  bool have_dest = false;

  while(1) {
    PROCESS_YIELD();

    battery_tick_and_maybe_log_dead();

    if(etimer_expired(&send_et)) {
      etimer_reset(&send_et);
      if(dead_logged) continue;

      if(!have_dest) {
        if(NETSTACK_ROUTING.node_is_reachable() &&
           NETSTACK_ROUTING.get_root_ipaddr(&dest)) {
          have_dest = true;
        }
      }

      if(TX.outstanding_acks >= APP_MAX_OUTSTANDING_ACKS) {
        TX.dropped++;
      } else {
        data_msg_t msg;
        msg.src_id = (uint8_t)node_id;
        msg.seq    = ++TX.seq;
        msg.t_ms   = now_ms();

        simple_udp_sendto(&udp_data, &msg, sizeof(msg), have_dest ? &dest : NULL);

        TX.sent++;
        TX.outstanding_acks++;
        TX.last_unacked_seq = msg.seq;
      }
    }

    if(etimer_expired(&stats_et)) {
      etimer_reset(&stats_et);

      const float qlr = ((TX.sent + TX.dropped) > 0) ?
        ((float)TX.dropped / (float)(TX.sent + TX.dropped)) : 0.0f;
      printf("METRIC QLR node=%u qlr=%.3f sent=%" PRIu32 " dropped=%" PRIu32 "\n",
             node_id, qlr, TX.sent, TX.dropped);

      const float prr_local = (TX.sent > 0) ? ((float)TX.acked / (float)TX.sent) : 0.0f;
      TX.prr_local_last = prr_local;
      printf("METRIC PRR_LOCAL node=%u prr=%.3f\n", node_id, prr_local);

      if(node_id == 1) {
        const float prr_root = (RX.expected_total > 0) ?
          ((float)RX.recv_total / (float)RX.expected_total) : 0.0f;
        printf("METRIC PRR_ROOT prr=%.3f sources=%" PRIu16 "\n",
               prr_root, RX.sources_active);

        RX.recv_total = 0;
        RX.expected_total = 0;
        RX.sources_active = 0;
        memset(RX.last_seq, 0, sizeof(RX.last_seq));
      }
    }

    (void)ev; (void)data;
  }

  PROCESS_END();
}

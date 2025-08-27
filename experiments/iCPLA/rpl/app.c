#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include "net/ipv6/uip.h"
#include "net/ipv6/uip-ds6.h"
#include "net/link-stats.h"
#include "sys/energest.h"
#include "sys/log.h"
#include "sys/node-id.h"
#include "dev/serial-line.h"
#include "random.h"

#include "rpl-icpla.h"

#include <string.h>
#include <stdbool.h>
#include <stdio.h>
#include <inttypes.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ---------------- Config ---------------- */
#define DATA_PORT             8765
#define CTRL_PORT             8766

#define SEND_INTERVAL_SEC     5
#define METRIC_INTERVAL_SEC   5

/* Energy model (rough CC2420-like) */
#define V_SUPPLY_V            3.0f
#define I_CPU_mA              1.8f
#define I_LPM_mA              0.054f
#define I_TX_mA               17.4f
#define I_RX_mA               18.8f
/* Energy budget in milli-Joules (per node) */
#ifndef ENERGY_BUDGET_mJ
#define ENERGY_BUDGET_mJ      (120000.0f) /* 120 J default */
#endif

/* Warmup gate (do not send until ALPHA applied, or timeout) */
#ifndef WARMUP_SEC
#define WARMUP_SEC            120
#endif

/* ---------------- Types ---------------- */
typedef struct {
  uint32_t seq;
  uint32_t ts_ticks; /* when sent (clock_time_t) */
} __attribute__((packed)) payload_hdr_t;

typedef struct {
  uint8_t  type;          /* 'A' for alpha update */
  uint16_t alpha_milli;   /* 350 => 0.35 */
} __attribute__((packed)) ctrl_alpha_t;

/* ---------------- Globals ---------------- */
static struct simple_udp_connection udp_data;
static struct simple_udp_connection udp_ctrl;

static uip_ipaddr_t root_addr;

static uint32_t seqno = 0;
static uint32_t sent_ok = 0;
static uint32_t dropped_app = 0;

static uint8_t alpha_ready = 0;
static clock_time_t warmup_deadline;

static float energy_mJ = 0.0f;
static uint8_t dead = 0;

static clock_time_t last_energest_flush = 0;

/* PRR at sink */
#define MAX_NODES 256
typedef struct {
  uint8_t seen;
  uint32_t first_seq;
  uint32_t last_seq;
  uint32_t rx_count;
} prr_entry_t;
static prr_entry_t prr_tbl[MAX_NODES];

/* ---------------- Utilities ---------------- */
static void
set_global_address(void)
{
  if(NETSTACK_ROUTING.node_is_root()) {
    uip_ipaddr_t ipaddr;
    /* aaaa::1 */
    uip_ip6addr(&ipaddr, 0xaaaa,0,0,0,0,0,0,0x0001);
    uip_ds6_addr_add(&ipaddr, 0, ADDR_MANUAL);
    memcpy(&root_addr, &ipaddr, sizeof(ipaddr));
  } else {
    /* root assumed at aaaa::1 */
    uip_ip6addr(&root_addr, 0xaaaa,0,0,0,0,0,0,0x0001);
  }
}

static void
energest_update_and_check(void)
{
  energest_flush();

  uint32_t cpu = energest_type_time(ENERGEST_TYPE_CPU);
  uint32_t lpm = energest_type_time(ENERGEST_TYPE_LPM);
  uint32_t tx  = energest_type_time(ENERGEST_TYPE_TRANSMIT);
  uint32_t rx  = energest_type_time(ENERGEST_TYPE_LISTEN);

  /* energy since last flush approximated by absolute totals; recompute full energy every time */
  float sec_cpu = (float)cpu / ENERGEST_SECOND;
  float sec_lpm = (float)lpm / ENERGEST_SECOND;
  float sec_tx  = (float)tx  / ENERGEST_SECOND;
  float sec_rx  = (float)rx  / ENERGEST_SECOND;

  /* mJ = V * mA * s */
  energy_mJ = V_SUPPLY_V * (
      I_CPU_mA * sec_cpu +
      I_LPM_mA * sec_lpm +
      I_TX_mA  * sec_tx  +
      I_RX_mA  * sec_rx) ;

  if(!dead && energy_mJ >= ENERGY_BUDGET_mJ) {
    dead = 1;
    LOG_INFO("METRIC NLT DEAD node=%u t_ms=%lu energy_mJ=%.1f\n",
             node_id, (unsigned long)(clock_time() * 1000UL / CLOCK_SECOND), energy_mJ);
  }
  (void)last_energest_flush;
}

/* ---------------- Callbacks ---------------- */
static void
data_rx_cb(struct simple_udp_connection *c,
           const uip_ipaddr_t *sender_addr,
           uint16_t sender_port,
           const uip_ipaddr_t *receiver_addr,
           uint16_t receiver_port,
           const uint8_t *data,
           uint16_t datalen)
{
  /* Sink receives data */
  if(!NETSTACK_ROUTING.node_is_root() || datalen < sizeof(payload_hdr_t)) {
    return;
  }
  const payload_hdr_t *ph = (const payload_hdr_t *)data;
  uint32_t sseq = ph->seq;
  /* Use last byte of IPv6 IID as src id (matches Cooja node_id in practice) */
  uint8_t sid = sender_addr->u8[15];

  uint32_t now_ticks = clock_time();
  uint32_t sent_ticks = ph->ts_ticks;
  uint32_t diff_ticks = (now_ticks - sent_ticks);
  uint32_t e2e_ms = (diff_ticks * 1000UL) / CLOCK_SECOND;

  /* PRR table update */
  prr_entry_t *e = &prr_tbl[sid];
  if(!e->seen) {
    e->seen = 1;
    e->first_seq = sseq;
    e->last_seq = sseq;
    e->rx_count = 1;
  } else {
    if(sseq > e->last_seq) e->last_seq = sseq;
    e->rx_count++;
  }

  LOG_INFO("METRIC E2E src=%u seq=%lu e2e_ms=%lu\n",
           sid, (unsigned long)sseq, (unsigned long)e2e_ms);
}

static void
ctrl_rx_cb(struct simple_udp_connection *c,
           const uip_ipaddr_t *sender_addr,
           uint16_t sender_port,
           const uip_ipaddr_t *receiver_addr,
           uint16_t receiver_port,
           const uint8_t *data,
           uint16_t datalen)
{
  if(datalen >= sizeof(ctrl_alpha_t)) {
    const ctrl_alpha_t *m = (const ctrl_alpha_t *)data;
    if(m->type == 'A') {
      icpla_set_alpha_milli(m->alpha_milli);
      alpha_ready = 1;
      LOG_INFO("ICPLA_ALPHA_APPLIED id=%u alpha=%.3f\n",
               node_id, icpla_get_alpha_milli() / 1000.0f);
    }
  }
}

/* ---------------- Process ---------------- */
PROCESS(app_process, "iCPLA app");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static struct etimer periodic;
  static struct etimer metric_timer;

  PROCESS_BEGIN();

  /* Set warmup deadline */
  warmup_deadline = clock_time() + (WARMUP_SEC * CLOCK_SECOND);

  /* If node 1, become RPL root */
  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
  }

  /* Set global address / root addr */
  set_global_address();

  /* Register sockets */
  simple_udp_register(&udp_data, DATA_PORT, NULL, DATA_PORT, data_rx_cb);
  simple_udp_register(&udp_ctrl, CTRL_PORT, NULL, CTRL_PORT, ctrl_rx_cb);

  /* Serial line for root to accept ALPHA= commands */
  serial_line_init();

  /* Initialize iCPLA controller */
  icpla_init(node_id);

  etimer_set(&periodic, SEND_INTERVAL_SEC * CLOCK_SECOND);
  etimer_set(&metric_timer, METRIC_INTERVAL_SEC * CLOCK_SECOND);

  while(1) {
    PROCESS_YIELD();

    if(ev == serial_line_event_message && data != NULL && node_id == 1) {
      char *line = (char *)data;
      /* Expected: ALPHA=0.350 */
      float aval = 0.0f;
      if(sscanf(line, "ALPHA=%f", &aval) == 1) {
        aval = (aval < 0.0f) ? 0.0f : (aval > 1.0f ? 1.0f : aval);
        uint16_t a_milli = (uint16_t)(aval * 1000.0f + 0.5f);
        icpla_set_alpha_milli(a_milli);
        alpha_ready = 1;

        /* Broadcast to all nodes */
        ctrl_alpha_t m = { .type='A', .alpha_milli=a_milli };
        uip_ipaddr_t dest;
        uip_create_linklocal_allnodes_mcast(&dest);
        simple_udp_sendto(&udp_ctrl, &m, sizeof(m), &dest);

        LOG_INFO("ICPLA_ALPHA_BCAST id=1 alpha=%.3f\n", aval);
      }
    }

    if(etimer_expired(&periodic)) {
      etimer_reset(&periodic);

      if(dead) {
        continue;
      }

      /* Optionally hold until alpha is received or warmup elapsed */
      if(!alpha_ready) {
        if(clock_time() < warmup_deadline) {
          LOG_INFO("APP HOLD id=%u waiting-for-RL\n", node_id);
          continue;
        } else {
          LOG_WARN("APP WARMUP timeout; proceeding without RL\n");
          alpha_ready = 1;
        }
      }

      /* Wait for route */
      if(!NETSTACK_ROUTING.node_is_root() &&
         !NETSTACK_ROUTING.node_is_reachable()) {
        LOG_INFO("APP HOLD id=%u waiting-for-route\n", node_id);
        continue;
      }

      /* App-level shedding: drop with probability act/10 (0..0.5) */
      uint8_t drop_tenths = icpla_get_drop_prob_tenths();
      uint16_t r = random_rand() % 10;
      if(r < drop_tenths) {
        dropped_app++;
        /* count as queued then dropped -> QLR increases */
      } else {
        /* Send a packet if not root */
        if(!NETSTACK_ROUTING.node_is_root()) {
          payload_hdr_t ph;
          ph.seq = ++seqno;
          ph.ts_ticks = clock_time();

          uip_ipaddr_t dest = root_addr;
          simple_udp_sendto(&udp_data, &ph, sizeof(ph), &dest);
          sent_ok++;
          LOG_INFO("SEND id=%u seq=%"PRIu32"\n", node_id, seqno);
        }
      }

      /* Energy accounting */
      energest_update_and_check();
    }

    if(etimer_expired(&metric_timer)) {
      etimer_reset(&metric_timer);

      /* Local QLR and PRR_LOCAL */
      uint32_t gen = sent_ok + dropped_app;
      float qlr = gen ? ((float)dropped_app / (float)gen) : 0.0f;
      float prr_local = gen ? (1.0f - qlr) : 0.0f;

      LOG_INFO("METRIC QLR node=%u qlr=%.3f sent=%"PRIu32" dropped=%"PRIu32"\n",
               node_id, qlr, sent_ok, dropped_app);
      LOG_INFO("METRIC PRR_LOCAL node=%u prr=%.3f\n", node_id, prr_local);

      if(NETSTACK_ROUTING.node_is_root()) {
        /* Compute PRR at sink: mean over sources */
        uint32_t sum_src = 0;
        float prr_sum = 0.0f;
        for(int i=0;i<MAX_NODES;i++) {
          prr_entry_t *e = &prr_tbl[i];
          if(e->seen) {
            uint32_t sent_est = (e->last_seq - e->first_seq + 1);
            if(sent_est > 0) {
              float prr = (float)e->rx_count / (float)sent_est;
              prr_sum += prr;
              sum_src++;
            }
          }
        }
        float prr_root = (sum_src>0) ? (prr_sum / sum_src) : 0.0f;
        LOG_INFO("METRIC PRR_ROOT prr=%.3f sources=%"PRIu32"\n", prr_root, sum_src);
      }
    }

  } /* while */

  PROCESS_END();
}

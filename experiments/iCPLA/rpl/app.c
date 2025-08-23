#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include "net/ipv6/uip.h"
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
#include <stdlib.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ------------ App config ------------ */
#define UDP_DATA_PORT        8765
#define UDP_CTRL_PORT        8766  /* for α updates broadcast */
#ifndef APP_CONF_PAYLOAD_SIZE
#define APP_CONF_PAYLOAD_SIZE 128
#endif
#define SEND_INTERVAL_SEC    5

/* Energy model */
#define V_SUPPLY_V          3.0f
#define I_CPU_mA            1.8f
#define I_TX_mA             17.4f
#define I_RX_mA             18.8f
#define ENERGY_BUDGET_mJ    (120000.0f)

/* ------------ Types ------------- */
typedef struct {
  uint32_t seq;
  uint32_t ts_ticks; /* when sent */
  uint8_t  pad[APP_CONF_PAYLOAD_SIZE - 8];
} __attribute__((packed)) payload_t;

typedef struct {
  uint8_t  type;          /* 'A' for alpha update */
  uint16_t alpha_milli;   /* 350 => 0.350 */
} __attribute__((packed)) ctrl_alpha_t;

/* ------------ State ------------- */
static struct simple_udp_connection udp_data;
static struct simple_udp_connection udp_ctrl;
static struct etimer periodic_timer;

static uint32_t generated = 0, sent_ok = 0, dropped = 0, recv_local = 0;
static float    qlr_smoothed = 0.0f;

static float total_energy_mJ = 0.0f;
static bool  is_dead = false;

static unsigned long last_cpu = 0, last_tx = 0, last_rx = 0;

/* Root-only aggregates */
static uint64_t root_recv_total = 0;
static uint64_t root_e2e_sum_ms = 0;

/* ------------ Helpers ------------- */
static void update_energy_and_maybe_die(void){
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
    LOG_INFO("METRIC NLT DEAD id=%u energy_mJ=%.1f\n", node_id, total_energy_mJ);
  }
}

static float energy_consumption_ratio(void){
  if(total_energy_mJ <= 0) return 0.0f;
  float e = total_energy_mJ / ENERGY_BUDGET_mJ;
  return e > 1.0f ? 1.0f : e;
}

static float best_neighbor_etx(void){
  const uip_ds6_nbr_t *nbr = uip_ds6_nbr_head();
  float best = 3.0f;
  while(nbr) {
    const linkaddr_t *ll = (const linkaddr_t *)uip_ds6_nbr_get_ll(nbr);
    const link_stats_t *st = link_stats_from_lladdr(ll);
    if(st) {
      float etx = (float)st->etx / LINK_STATS_ETX_DIVISOR;
      if(etx < best) best = etx;
    }
    nbr = uip_ds6_nbr_next(nbr);
  }
  return best;
}

static void report_period_metrics(void){
  float q_now = (generated == 0) ? 0.0f : ((float)dropped / (float)generated);
  qlr_smoothed = (0.9f * qlr_smoothed) + (0.1f * q_now);
  float prr_local = (generated == 0) ? 0.0f : ((float)sent_ok / (float)generated);
  float ecr = energy_consumption_ratio();

  LOG_INFO("METRIC QLR id=%u gen=%lu sent=%lu drop=%lu qlr=%.3f ecr=%.3f E_mJ=%.1f alpha=%.3f\n",
           node_id, (unsigned long)generated, (unsigned long)sent_ok, (unsigned long)dropped,
           qlr_smoothed, ecr, total_energy_mJ, icpla_get_alpha_milli()/1000.0f);

  LOG_INFO("METRIC PRR_LOCAL id=%u prr=%.3f\n", node_id, prr_local);

  if(node_id == 1) {
    float e2e_avg = (root_recv_total == 0) ? 0.0f : ((float)root_e2e_sum_ms / (float)root_recv_total);
    LOG_INFO("METRIC PRR_ROOT recv=%llu E2E_AVG_MS=%.1f\n",
      (unsigned long long)root_recv_total, e2e_avg);
  }
}

/* ------------ UDP receive handlers ------------- */
static void
data_recv_cb(struct simple_udp_connection *c,
             const uip_ipaddr_t *sender_addr, uint16_t sender_port,
             const uip_ipaddr_t *receiver_addr, uint16_t receiver_port,
             const uint8_t *data, uint16_t datalen)
{
  (void)c; (void)sender_port; (void)receiver_port;
  recv_local++;

  if(datalen >= sizeof(payload_t)) {
    const payload_t *p = (const payload_t *)data;
    clock_time_t now_ticks = clock_time();
    uint32_t dt_ticks = (uint32_t)(now_ticks - p->ts_ticks);
    uint32_t dt_ms = (dt_ticks * 1000ul) / CLOCK_SECOND;

    if(node_id == 1) {
      root_recv_total++;
      root_e2e_sum_ms += dt_ms;
      LOG_INFO("METRIC E2E id=%u seq=%lu e2e_ms=%lu\n",
               node_id, (unsigned long)p->seq, (unsigned long)dt_ms);
    }
  }

  LOG_INFO("RECV id=%u bytes=%u from ", node_id, datalen);
  LOG_INFO_6ADDR(sender_addr);
  LOG_INFO_("\n");
}

static void
ctrl_recv_cb(struct simple_udp_connection *c,
             const uip_ipaddr_t *sender_addr, uint16_t sender_port,
             const uip_ipaddr_t *receiver_addr, uint16_t receiver_port,
             const uint8_t *data, uint16_t datalen)
{
  (void)c; (void)sender_port; (void)receiver_port;
  if(datalen >= sizeof(ctrl_alpha_t)) {
    const ctrl_alpha_t *m = (const ctrl_alpha_t *)data;
    if(m->type == 'A') {
      icpla_set_alpha_milli(m->alpha_milli);
      LOG_INFO("ICPLA_ALPHA_APPLIED id=%u alpha=%.3f (from ", node_id, m->alpha_milli/1000.0f);
      LOG_INFO_6ADDR(sender_addr); LOG_INFO_(")\n");
    }
  }
}

/* ------------ Process ------------- */
PROCESS(app_process, "iCPLA(+RL) app w/ runtime alpha");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static payload_t pkt;
  static uip_ipaddr_t dest;

  PROCESS_BEGIN();

  icpla_init(node_id);

  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
    LOG_INFO("ROOT STARTED (node 1)\n");
  }

  /* UDP sockets */
  simple_udp_register(&udp_data, UDP_DATA_PORT, NULL, UDP_DATA_PORT, data_recv_cb);
  simple_udp_register(&udp_ctrl, UDP_CTRL_PORT, NULL, UDP_CTRL_PORT, ctrl_recv_cb);

  /* Serial line (root receives 'ALPHA=0.35' from Python) */
  serial_line_init();

  memset(&pkt, 0, sizeof(pkt));
  etimer_set(&periodic_timer, CLOCK_SECOND * SEND_INTERVAL_SEC);

  while(1) {
    PROCESS_YIELD();

    if(ev == PROCESS_EVENT_TIMER && data == &periodic_timer) {
      etimer_reset(&periodic_timer);

      /* Energy + life */
      update_energy_and_maybe_die();

      /* Observe for local RL */
      float ecr = energy_consumption_ratio();
      float etx = best_neighbor_etx();
      icpla_observe(qlr_smoothed, etx, ecr, 0.0f,
                    (uint32_t)((clock_time()*1000ul)/CLOCK_SECOND));

      /* Periodic report */
      report_period_metrics();

      if(!is_dead) {
        if(NETSTACK_ROUTING.node_is_reachable() &&
           NETSTACK_ROUTING.get_root_ipaddr(&dest)) {

          /* RL-controlled shedding */
          uint8_t drop_tenths = icpla_get_drop_prob_tenths();
          if((random_rand()%10) < drop_tenths) {
            dropped++;
            LOG_INFO("DROP id=%u a=%u\n", node_id, (unsigned)drop_tenths);
          } else {
            pkt.seq = generated + 1;
            pkt.ts_ticks = clock_time();
            simple_udp_sendto(&udp_data, &pkt, sizeof(pkt), &dest);
            sent_ok++;
            LOG_INFO("SEND id=%u seq=%lu a=%u etx=%.2f qlr=%.3f ecr=%.3f alpha=%.3f\n",
                     node_id, (unsigned long)pkt.seq, (unsigned)drop_tenths,
                     etx, qlr_smoothed, ecr, icpla_get_alpha_milli()/1000.0f);
          }
          generated++;
        } else {
          LOG_INFO("NO_ROUTE id=%u\n", node_id);
        }
      }
    }

    /* Root: handle SerialSocket commands and multicast α */
    if(ev == serial_line_event_message && node_id == 1) {
      const char *s = (const char *)data;
      if(strncmp(s, "ALPHA=", 6) == 0) {
        float aval = (float)atof(s+6);
        if(aval < 0.0f) aval = 0.0f; if(aval > 1.0f) aval = 1.0f;
        uint16_t a_milli = (uint16_t)(aval * 1000.0f + 0.5f);

        /* Apply locally and broadcast */
        icpla_set_alpha_milli(a_milli);

        ctrl_alpha_t m = { .type='A', .alpha_milli=a_milli };
        uip_create_linklocal_allnodes_mcast(&dest);
        simple_udp_sendto(&udp_ctrl, &m, sizeof(m), &dest);

        LOG_INFO("ICPLA_ALPHA_BCAST id=1 alpha=%.3f\n", aval);
      }
    }
  }

  PROCESS_END();
}

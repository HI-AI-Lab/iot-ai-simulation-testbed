/* same contents as OF0/app.c */
/* Same base as your original app.c, with minimal additions for PRR_GLOBAL + E2E */

#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "sys/energest.h"
#include "sys/log.h"
#include "random.h"
#include "sys/node-id.h"
#include "project-conf.h"   /* for SEND_INTERVAL_SEC */
#include <string.h>         /* memcpy, memset */

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_PORT              8765
#define PAYLOAD_SIZE          128
#define DROP_PROB_PER_TEN     2

#define V_SUPPLY_V            3.0f
#define I_CPU_mA              1.8f
#define I_TX_mA               17.4f
#define I_RX_mA               18.8f

#define ENERGY_BUDGET_mJ      (120000.0f)

static struct simple_udp_connection udp_conn;
static struct etimer periodic_timer;

static uint32_t generated = 0;
static uint32_t received  = 0;
static uint32_t dropped   = 0;

static float total_energy_mJ = 0.0f;
static bool is_dead = false;

static unsigned long last_cpu = 0;
static unsigned long last_tx  = 0;
static unsigned long last_rx  = 0;

/* ------------------ Added: tiny header + sink-only accumulators ------------------ */
#define MAX_NODES_TRACKED 256
typedef struct __attribute__((packed)) {
  uint16_t src_id;   /* sender node_id */
  uint32_t seq;      /* sender's per-packet sequence */
  uint32_t t_ms;     /* sender's send timestamp (ms) */
} pkt_hdr_t;

/* Sink-only (node_id==1) bookkeeping */
static uint32_t sink_recv_total = 0;
static uint32_t sink_e2e_sum_ms = 0;
static uint32_t sink_e2e_samples = 0;
static uint32_t sink_max_seq[MAX_NODES_TRACKED]; /* highest seq seen per src */
/* ------------------------------------------------------------------------------- */

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

  last_cpu = cpu_now;
  last_tx  = tx_now;
  last_rx  = rx_now;

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
    LOG_INFO("METRIC NLT DEAD node=%u t_ms=%lu energy_mJ=%.1f\n",
             node_id, (unsigned long)(clock_time() * (1000UL / CLOCK_SECOND)), total_energy_mJ);
  }
}

static void
report_qlr(void)
{
  const float qlr = (generated == 0) ? 0.0f : ((float)dropped / (float)generated);
  LOG_INFO("METRIC QLR node=%u qlr=%.3f sent=%lu dropped=%lu\n",
           node_id, qlr, (unsigned long)generated, (unsigned long)dropped);
}

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

  LOG_INFO("DEBUG RECV: node=%u datalen=%d\n", node_id, datalen);

  received++;
  const float prr_local = (generated == 0) ? 0.0f : ((float)received / (float)generated);
  LOG_INFO("METRIC PRR_LOCAL node=%u prr=%.3f\n", node_id, prr_local);

  /* ------------------ Added: global PRR + E2E at the sink only ------------------ */
  if(node_id == 1 && data != NULL && datalen >= (int)sizeof(pkt_hdr_t)) {
    pkt_hdr_t hdr;
    memcpy(&hdr, data, sizeof(hdr));

    if(hdr.src_id < MAX_NODES_TRACKED) {
      /* E2E in ms */
      uint32_t now_ms = (uint32_t)(clock_time() * (1000UL / CLOCK_SECOND));
      uint32_t e2e_ms = now_ms - hdr.t_ms;
      sink_e2e_sum_ms += e2e_ms;
      sink_e2e_samples++;

      /* PRR global */
      sink_recv_total++;
      if(hdr.seq > sink_max_seq[hdr.src_id]) {
        sink_max_seq[hdr.src_id] = hdr.seq;
      }

      /* expected_total = sum(max_seq_seen + 1) across sources observed */
      uint32_t expected_total = 0;
      for(uint16_t i = 0; i < MAX_NODES_TRACKED; i++) {
        if(sink_max_seq[i] > 0 || (i == hdr.src_id)) {
          expected_total += (sink_max_seq[i] + 1);
        }
      }
      float prr_global = (expected_total > 0) ? ((float)sink_recv_total / (float)expected_total) : 0.0f;
      float e2e_avg_ms = (sink_e2e_samples > 0) ? ((float)sink_e2e_sum_ms / (float)sink_e2e_samples) : 0.0f;

      LOG_INFO("METRIC PRR_GLOBAL prr=%.3f recv=%lu expected=%lu\n",
               prr_global, (unsigned long)sink_recv_total, (unsigned long)expected_total);
      LOG_INFO("METRIC E2E avg_ms=%.2f samples=%lu\n",
               e2e_avg_ms, (unsigned long)sink_e2e_samples);
    }
  }
  /* ----------------------------------------------------------------------------- */
}

PROCESS(app_process, "OF0: UDP sender/receiver with energy + QLR + PRR/E2E");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static uint8_t payload[PAYLOAD_SIZE];
//  static uip_ipaddr_t dest;

  PROCESS_BEGIN();

  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
    LOG_INFO("ROOT STARTED (node 1)\n");
    /* zero sink tracking */
    memset(sink_max_seq, 0, sizeof(sink_max_seq));
    sink_recv_total = 0;
    sink_e2e_sum_ms = 0;
    sink_e2e_samples = 0;
  }

  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, recv_cb);

  /* keep payload size; header is written before each send */
  memset(payload, 0, sizeof(payload));

  energest_flush();
  last_cpu = energest_type_time(ENERGEST_TYPE_CPU);
  last_tx  = energest_type_time(ENERGEST_TYPE_TRANSMIT);
  last_rx  = energest_type_time(ENERGEST_TYPE_LISTEN);

  etimer_set(&periodic_timer, CLOCK_SECOND * SEND_INTERVAL_SEC);

  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));
    etimer_reset(&periodic_timer);

    update_energy_and_maybe_die();

    if(is_dead) {
      report_qlr();
      continue;
    }

	/* DEBUG: force send without routing check */
	int r = random_rand() % 10;
	if(r < DROP_PROB_PER_TEN) {
	  dropped++;
	} else {
	  static uint32_t seq_counter = 0;
	  pkt_hdr_t hdr;
	  hdr.src_id = (uint16_t)node_id;
	  hdr.seq    = seq_counter++;
	  hdr.t_ms   = (uint32_t)(clock_time() * (1000UL / CLOCK_SECOND)); /* ms */

	  memcpy(payload, &hdr, sizeof(hdr));

	  LOG_INFO("DEBUG SEND: node=%u (forced)\n", node_id);

	  /* Send to unspecified/broadcast — will at least exercise radios */
	  uip_ipaddr_t dest_ip;
	  uip_create_linklocal_allnodes_mcast(&dest_ip);
	  simple_udp_sendto(&udp_conn, payload, sizeof(payload), &dest_ip);
	  generated++;
	}

    report_qlr();
  }

  PROCESS_END();
}

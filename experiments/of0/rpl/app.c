#include "contiki.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "sys/energest.h"
#include "sys/log.h"
#include "random.h"
#include "sys/node-id.h"

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ----------------------- Config ----------------------- */
#define UDP_PORT              8765
#define SEND_INTERVAL_SEC     5          /* how often to attempt a send */
#define PAYLOAD_SIZE          128        /* bytes */
#define DROP_PROB_PER_TEN     2          /* 0..10 (e.g., 2 => ~20% drop) */

/* Energy model (replace with platform-accurate numbers) */
#define V_SUPPLY_V            3.0f
#define I_CPU_mA              1.8f
#define I_TX_mA               17.4f
#define I_RX_mA               18.8f

/* Energy budget: when exceeded, mote is considered dead */
#define ENERGY_BUDGET_mJ      (120000.0f) /* example: 120 J => 120,000 mJ */

/* -------------------- State --------------------------- */
static struct simple_udp_connection udp_conn;
static struct etimer periodic_timer;

static uint32_t generated = 0;
static uint32_t received  = 0;
static uint32_t dropped   = 0;

static float total_energy_mJ = 0.0f;
static bool is_dead = false;

/* Energest snapshots */
static unsigned long last_cpu = 0;
static unsigned long last_tx  = 0;
static unsigned long last_rx  = 0;

/* -------------- Utilities / Helpers ------------------- */
static void
update_energy_and_maybe_die(void)
{
  /* Flush latest accounting */
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

  /* Convert ticks -> seconds */
  const float TICKS_PER_SEC = (float)ENERGEST_SECOND;
  float cpu_s = cpu_diff / TICKS_PER_SEC;
  float tx_s  = tx_diff  / TICKS_PER_SEC;
  float rx_s  = rx_diff  / TICKS_PER_SEC;

  /* Energy (mJ) = V * I(A) * t(s) * 1000 */
  float energy_period_mJ = V_SUPPLY_V * (
    (I_CPU_mA/1000.0f) * cpu_s +
    (I_TX_mA /1000.0f) * tx_s  +
    (I_RX_mA /1000.0f) * rx_s
  ) * 1000.0f;

  total_energy_mJ += energy_period_mJ;

  if(!is_dead && total_energy_mJ >= ENERGY_BUDGET_mJ) {
    is_dead = true;
    LOG_INFO("METRIC NLT DEAD node=%u t_ms=%lu energy_mJ=%.1f\n",
             node_id, (unsigned long) (clock_time() * (1000UL / CLOCK_SECOND)), total_energy_mJ);
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
  (void)c; (void)sender_port; (void)receiver_port; (void)data; (void)datalen;
  (void)receiver_addr;
  received++;
  /* Local PRR metric example */
  const float prr_local = (generated == 0) ? 0.0f : ((float)received / (float)generated);
  LOG_INFO("METRIC PRR_LOCAL node=%u prr=%.3f\n", node_id, prr_local);
}

/* -------------------- Process ------------------------- */
PROCESS(app_process, "OF0: UDP sender/receiver with energy + QLR");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static uint8_t payload[PAYLOAD_SIZE];
  static uip_ipaddr_t dest;

  PROCESS_BEGIN();

  /* Root setup on node 1 */
  if(node_id == 1) {
    NETSTACK_ROUTING.root_start();
    LOG_INFO("ROOT STARTED (node 1)\n");
  }

  /* UDP init */
  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, recv_cb);

  for(size_t i = 0; i < sizeof(payload); i++) {
    payload[i] = (uint8_t)(i & 0xFF);
  }

  /* Initialize energetst baselines once */
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
      continue; /* Dead mote does not send */
    }

    if(NETSTACK_ROUTING.node_is_reachable() &&
       NETSTACK_ROUTING.get_root_ipaddr(&dest)) {

      int r = random_rand() % 10; /* 0..9 */
      if(r < DROP_PROB_PER_TEN) {
        dropped++;
      } else {
        simple_udp_sendto(&udp_conn, payload, sizeof(payload), &dest);
        generated++;
      }
    }

    report_qlr();
  }

  PROCESS_END();
}

#include "contiki.h"
#include "random.h"
#include "net/routing/routing.h"
#include "net/routing/rpl-lite/rpl.h"  /* preferred parent API */
#include "net/netstack.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6.h"
#include "sys/log.h"
#include "node-id.h"
#include "positions-simulation.h"
#include <stdint.h>
#include <inttypes.h>
#include <math.h>

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT	8765
#define UDP_SERVER_PORT	5678

#define SIM_END_MS       5000000UL   // total runtime in ms (e.g. 5000s = ~83 min)

/* Default if not provided from Makefile */
/*
#ifndef SEND_INTERVAL_MS
#define SEND_INTERVAL_MS 750  // ~80 PPM
#endif
*/

/* === Energy Model (Lei & Liu 2024) === */
#define INIT_ENERGY_J   10.0
#define E_ELEC          50e-9      /* 50 nJ/bit */
#define EPS_FS          10e-12     /* 10 pJ/bit/m^2 */
#define EPS_MP          10e-12     /* 10 pJ/bit/m^4  (adjust if your paper uses a different value) */
#define PKT_BITS        (128*8)    /* 128 B = 1024 bits */

static double residual_energy = INIT_ENERGY_J;

/* Distance between two nodes by ID, using generated positions header */
static inline double distance_nodes(unsigned id1, unsigned id2) {
  double dx = (double)node_pos_x[id1] - (double)node_pos_x[id2];
  double dy = (double)node_pos_y[id1] - (double)node_pos_y[id2];
  return sqrt(dx*dx + dy*dy);
}

/* TX energy (Joules) for one 128B packet to distance d (m) */
static inline double tx_energy(double d) {
  double dth = sqrt(EPS_FS / EPS_MP);
  if(d <= dth) {
    return PKT_BITS * (E_ELEC + EPS_FS * d * d);
  } else {
    return PKT_BITS * (E_ELEC + EPS_MP * d * d * d * d);
  }
}

/* RX energy (Joules) for one 128B packet — (we’ll use this in the next step for forwarding) */
static inline double rx_energy(void) {
  return PKT_BITS * E_ELEC;
}

/* === Energy Model (Lei & Liu 2024) === */

static struct simple_udp_connection udp_conn;
static uint32_t rx_count = 0;

typedef struct {
  uint32_t t_sent;       // send timestamp (ms, from clock_time)
  uint8_t  padding[124]; // filler to make total size = 128 bytes
} __attribute__((packed)) app_packet_t;

/* Return an exponential( mean = SEND_INTERVAL_MS ) delay in Contiki ticks */
static clock_time_t
poisson_next_delay_ticks(void)
{
  /* U in (0,1]; avoid 0 to protect logf */
  float u = ((float)random_rand() + 1.0f) / ((float)RANDOM_RAND_MAX + 1.0f);

  /* mean seconds = SEND_INTERVAL_MS / 1000.0 */
  float mean_sec = (float)SEND_INTERVAL_MS / 1000.0f;

  /* exponential sample: X = -mean * ln(U)  (seconds) */
  float x_sec = -mean_sec * logf(u);

  /* convert to ticks */
  clock_time_t ticks = (clock_time_t)(x_sec * (float)CLOCK_SECOND);

  /* never return 0 ticks */
  if(ticks < 1) ticks = 1;

  return ticks;
}

static void wrapup(uint32_t tx_count, uint32_t missed_tx_count) {
  LOG_INFO("WRAPUP node_id=%u Tx=%"PRIu32" Rx=%"PRIu32" Missed=%"PRIu32" residual=%.6fJ\n",
           node_id, tx_count, rx_count, missed_tx_count, residual_energy);
}

/* Map IPv6 -> node_id (Cooja: last 16 bits = node_id, in hex) */
static unsigned ip_to_nodeid(const uip_ipaddr_t *ip) {
  return (unsigned)UIP_HTONS(ip->u16[7]);
}

/* Get our current preferred parent node_id, fallback = root (1) */
static unsigned get_parent_id(void) {
  rpl_instance_t *inst = rpl_get_default_instance();
  rpl_dag_t *dag = rpl_get_any_dag();
  if(inst && dag && dag->preferred_parent) {
    const uip_ipaddr_t *p_ip = rpl_parent_get_ipaddr(dag->preferred_parent);
    return ip_to_nodeid(p_ip);
  }
  return 1; // fallback to root
}

/*---------------------------------------------------------------------------*/
PROCESS(udp_client_process, "NODE");
AUTOSTART_PROCESSES(&udp_client_process);
/*---------------------------------------------------------------------------*/
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(udp_client_process, ev, data)
{
  static struct etimer periodic_timer;
  uip_ipaddr_t dest_ipaddr;
  static uint32_t tx_count;
  static uint32_t missed_tx_count;

  PROCESS_BEGIN();
  
  unsigned long ppm = (SEND_INTERVAL_MS > 0) ? 60000UL / (unsigned long)SEND_INTERVAL_MS : 0;
  LOG_INFO("mean_interval_ms=%lu (~PPM=%lu) [Poisson]\n",(unsigned long)SEND_INTERVAL_MS, ppm);

  /* Initialize UDP connection */
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                      UDP_SERVER_PORT, NULL);

  /* before loop: schedule first send with Poisson gap */
  etimer_set(&periodic_timer, poisson_next_delay_ticks());
  
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));
	
    uint32_t now_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
    if(now_ms > (SIM_END_MS)) {
        wrapup(tx_count,missed_tx_count);
		PROCESS_EXIT();
    }

    if(NETSTACK_ROUTING.node_is_reachable() &&
        NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {
      /* Send to DAG root */
      app_packet_t pkt;
      pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
      memset(pkt.padding, 0, sizeof(pkt.padding));
      simple_udp_sendto(&udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);
      tx_count++;
	  
      unsigned parent_id = get_parent_id();
      double d = distance_nodes(node_id, parent_id);
      residual_energy -= tx_energy(d);
      if(residual_energy <= 0) {
          residual_energy = 0;
          wrapup(tx_count,missed_tx_count);
		  PROCESS_EXIT();
      }
    } else {
      LOG_INFO("Not reachable yet\n");
      if(tx_count > 0) {
        missed_tx_count++;
      }
    }

    /* inside the loop after you handle a send attempt */
    etimer_set(&periodic_timer, poisson_next_delay_ticks());
  }

  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

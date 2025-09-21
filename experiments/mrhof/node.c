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

/* === Energy Model (Lei & Liu 2024) === */
#define INIT_ENERGY_J   10.0
#define E_ELEC          50e-9      /* 50 nJ/bit */
#define EPS_FS          10e-12     /* 10 pJ/bit/m^2 */
#define EPS_MP          10e-12     /* 10 pJ/bit/m^4  (adjust if your paper uses a different value) */
#define PKT_BITS        (128*8)    /* 128 B = 1024 bits */
/* === Energy Model (Lei & Liu 2024) === */

/*MOTE STATE*/
typedef enum {
  END_NONE   = 0,   /* still running */
  END_ENERGY = 1,   /* died due to energy depletion */
  END_TIME   = 2    /* simulation time reached */
} end_reason_t;

typedef struct {
  uint32_t tx_count;
  uint32_t rx_count;
  uint32_t missed_tx_count;
  double   residual_energy;
  end_reason_t end_reason;
  unsigned long end_time_ms;
  uint32_t ppm;
} mote_state_t;

static mote_state_t state = {
  .tx_count = 0,
  .rx_count = 0,
  .missed_tx_count = 0,
  .residual_energy = INIT_ENERGY_J,
  .end_reason = END_NONE,
  .end_time_ms = 0,
  .ppm = (SEND_INTERVAL_MS > 0) ? (60000UL / (unsigned long)SEND_INTERVAL_MS) : 0
};

static const char *
end_reason_str(end_reason_t r) {
  switch(r) {
    case END_ENERGY: return "energy";
    case END_TIME:   return "time";
    default:         return "none";
  }
}

static void
wrapup(void) {
  LOG_INFO("WRAPUP node_id=%u reason=%s end_ms=%lu "
         "Tx=%"PRIu32" Rx=%"PRIu32" Missed=%"PRIu32" residual=%.6fJ ppm=%"PRIu32"\n",
         node_id,
         end_reason_str(state.end_reason),
         state.end_time_ms,
         state.tx_count, state.rx_count, state.missed_tx_count,
         state.residual_energy,
         state.ppm);
}
/*MOTE STATE*/

/* === Energy Model (Lei & Liu 2024) === */
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

typedef struct {
  uint32_t t_sent;       // send timestamp (ms, from clock_time)
  uint8_t  padding[124]; // filler to make total size = 128 bytes
} __attribute__((packed)) app_packet_t;

/* Return an exponential delay in Contiki ticks */
static clock_time_t
poisson_next_delay_ticks(void)
{
  /* U in (0,1]; avoid 0 to protect logf */
  float u = ((float)random_rand() + 1.0f) / ((float)RANDOM_RAND_MAX + 1.0f);
  /* mean seconds = 60 / PPM  (packets per minute -> sec between packets) */
  float mean_sec;
  if(state.ppm > 0) {
    mean_sec = 60.0f / (float)state.ppm;
  } else {
    mean_sec = 1.0f;  /* fallback */
  }
  /* exponential sample: X = -mean * ln(U)  (seconds) */
  float x_sec = -mean_sec * logf(u);
  /* convert to ticks */
  clock_time_t ticks = (clock_time_t)(x_sec * (float)CLOCK_SECOND);
  /* never return 0 ticks */
  if(ticks < 1) ticks = 1;
  return ticks;
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

/* Return 1 if simulation time has passed, else 0 */
static int is_simulation_time_over(void) {
  uint32_t now_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
  return now_ms > SIM_END_MS;
}

/* Return 1 if energy is depleted; also update state */
static int is_energy_depleted(void) {
  if(state.residual_energy <= 0) {
    state.residual_energy = 0;   /* clamp */
    state.end_reason = END_ENERGY;
    state.end_time_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
    return 1;
  }
  return 0;
}

/* Prepare, send, update counters and energy */
static void
send_app_packet(void) {
  uip_ipaddr_t dest_ipaddr;

  if(NETSTACK_ROUTING.node_is_reachable() &&
     NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {

    /* build packet */
    app_packet_t pkt;
    pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
    memset(pkt.padding, 0, sizeof(pkt.padding));

    /* send to DAG root (RPL handles forwarding via parent) */
    simple_udp_sendto(&udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);

    /* update TX counter */
    state.tx_count++;

    /* account TX energy against preferred parent */
    unsigned parent_id = get_parent_id();
    double d = distance_nodes(node_id, parent_id);
    state.residual_energy -= tx_energy(d);

  } else {
    /* not yet reachable: after first send, count these as misses */
    if(state.tx_count > 0) {
      state.missed_tx_count++;
    }
  }
}

/*---------------------------------------------------------------------------*/
PROCESS(udp_client_process, "NODE");
AUTOSTART_PROCESSES(&udp_client_process);
/*---------------------------------------------------------------------------*/
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(udp_client_process, ev, data)
{
  static struct etimer periodic_timer;
  PROCESS_BEGIN();
  /* Initialize UDP connection */
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                      UDP_SERVER_PORT, NULL);
  /* before loop: schedule first send with Poisson gap */
  etimer_set(&periodic_timer, poisson_next_delay_ticks());
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));
    send_app_packet();
	if(is_energy_depleted() || is_simulation_time_over()) {
        wrapup();
		PROCESS_EXIT();
    }
    /* inside the loop after you handle a send attempt */
    etimer_set(&periodic_timer, poisson_next_delay_ticks());
  }
  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

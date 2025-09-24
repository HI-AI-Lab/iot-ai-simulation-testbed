#include "contiki.h"
#include "random.h"
#include "net/routing/routing.h"
#include "net/routing/rpl-lite/rpl.h"  /* preferred parent API */
#include "net/netstack.h"
#include "net/mac/mac.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6.h"
#include "sys/log.h"
#include "node-id.h"
#include "positions-simulation.h"
#include <stdint.h>
#include <inttypes.h>
#include <math.h>
#include "net/packetbuf.h"

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT	8765
#define UDP_SERVER_PORT	5678

#define SIM_END_MS       5000000UL   // total runtime in ms (e.g. 5000s = ~83 min)

/* === Energy Model (Lei & Liu 2024) === */
#define INIT_ENERGY_J   2000.0
#define E_ELEC          50e-9      /* 50 nJ/bit */
#define EPS_FS          10e-12     /* 10 pJ/bit/m^2 */
#define EPS_MP          10e-12     /* 10 pJ/bit/m^4  (adjust if your paper uses a different value) */
#define PKT_BITS        (128*8)    /* 128 B = 1024 bits */
/* === Energy Model (Lei & Liu 2024) === */

uint32_t toggle_value = 0;

/*MOTE STATE*/
typedef enum {
  END_NONE   = 0,   /* still running */
  END_ENERGY = 1,   /* died due to energy depletion */
  END_TIME   = 2    /* simulation time reached */
} end_reason_t;

typedef struct {
  uint32_t t_sent;
  uint16_t origin_id;
  char     padding[118];
} app_packet_t;

typedef struct {
  uint32_t qsize;
  uint32_t gen_count;
  uint32_t fwd_count;
  uint32_t q_loss_count;
  double   residual_energy;
  end_reason_t end_reason;
  uint32_t end_time_ms;
  uint32_t ppm;
  unsigned last_parent_id;
  uint32_t parent_switches;
} mote_state_t;

static mote_state_t state = {
  .qsize = QUEUEBUF_CONF_NUM,
  .gen_count = 0,
  .fwd_count = 0,
  .q_loss_count = 0,
  .residual_energy = INIT_ENERGY_J,
  .end_reason = END_NONE,
  .end_time_ms = 0,
  .ppm = (SEND_INTERVAL_MS > 0) ? (60000UL / (unsigned long)SEND_INTERVAL_MS) : 0,
  .last_parent_id = 0,
  .parent_switches = 0
};
/*
static const char *
end_reason_str(end_reason_t r) {
  switch(r) {
    case END_ENERGY: return "energy";
    case END_TIME:   return "time";
    default:         return "none";
  }
}*/
/*
static void
wrapup(void) {
	LOG_INFO("WRAPUP node_id=%u reason=%s end_ms=%" PRIu32 " "
			 "Gen=%" PRIu32 " Fwd=%" PRIu32 " QLoss=%" PRIu32 " qsize=%" PRIu32 " "
			 "residual=%.6fJ ppm=%" PRIu32 " parent=%u switches=%" PRIu32 "\n",
			 (unsigned int)node_id,
			 end_reason_str(state.end_reason),
			 state.end_time_ms,
			 state.gen_count,
			 state.fwd_count,
			 state.q_loss_count,
			 state.qsize,
			 state.residual_energy,
			 state.ppm,
			 state.last_parent_id,
			 state.parent_switches);
}
*/
/*MOTE STATE*/

/* Distance between two nodes by ID, using generated positions header */
static inline double
distance_nodes(unsigned id1, unsigned id2) {
  double dx = (double)node_pos_x[id1] - (double)node_pos_x[id2];
  double dy = (double)node_pos_y[id1] - (double)node_pos_y[id2];
  return sqrt(dx*dx + dy*dy);
}

/* TX energy (Joules) for one 128B packet to distance d (m) */
static inline double
tx_energy(double d, uint32_t bits) {
  double dth = sqrt(EPS_FS / EPS_MP);
  if(d <= dth) {
    // Free space model for short distances
    return (double)bits * (E_ELEC + EPS_FS * d * d);
  } else {
    // Multi-path fading model for longer distances
    return (double)bits * (E_ELEC + EPS_MP * d * d * d * d);
  }
}

/* RX energy (Joules) for one 128B packet — (we’ll use this in the next step for forwarding) */
static inline double
rx_energy(uint32_t bits) {
  return (double)bits * E_ELEC;
}

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
static unsigned
ip_to_nodeid(const uip_ipaddr_t *ip) {
  return (unsigned)UIP_HTONS(ip->u16[7]);
}

/* Get our current preferred parent node_id, fallback = 0 (none) */
static unsigned
get_parent_id(void) {
  rpl_dag_t *dag = rpl_get_any_dag();
  if(dag && dag->preferred_parent) {
    const uip_ipaddr_t *p_ip = rpl_parent_get_ipaddr(dag->preferred_parent);
    return ip_to_nodeid(p_ip);
  }
  return -1; // no parent
}

/* Return 1 if simulation time is over; also update state */
static int
is_simulation_time_over(void) {
  uint32_t now_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);

  if(now_ms >= SIM_END_MS) {
    state.end_reason = END_TIME;
    state.end_time_ms = now_ms;
    return 1;
  }
  return 0;
}

/* Return 1 if energy is depleted; also update state */
static int
is_energy_depleted(void) {
  if(state.residual_energy <= 0) {
    state.residual_energy = 0;
    state.end_reason = END_ENERGY;
    state.end_time_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
    return 1;
  }
  return 0;
}

static void
send_a_packet(struct simple_udp_connection *udp_conn) {
  uip_ipaddr_t dest_ipaddr;
  if(!NETSTACK_ROUTING.node_is_reachable() ||
     !NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {
    return; /* not reachable, skip this round */
  }
  app_packet_t pkt;
  pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
  pkt.origin_id = node_id;
  memset(pkt.padding, 0, sizeof(pkt.padding));
  /* transmit it */
  simple_udp_sendto(udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);
  state.gen_count++;
}

static void sniff_input(void) {
  uint16_t len = packetbuf_datalen();
  state.residual_energy -= rx_energy(len*8);
}

static void sniff_output(int mac_status) {
  if(mac_status == MAC_TX_QUEUE_FULL) {
    state.q_loss_count++;
  } else {
    unsigned parent_id = get_parent_id();
    if(parent_id != (unsigned)-1) {
	  state.fwd_count++;
      double d = distance_nodes(node_id, parent_id);
      uint16_t len = packetbuf_datalen();
      state.residual_energy -= tx_energy(d, len*8);
	  if(parent_id!=state.last_parent_id){
		  state.last_parent_id = parent_id;
		  state.parent_switches++;
	  }
    }
  }
}

NETSTACK_SNIFFER(my_sniffer, sniff_input, sniff_output);

static struct simple_udp_connection udp_conn;

/*---------------------------------------------------------------------------*/
/* Two Processes one for packet generation and one is for queue              */ 
/*---------------------------------------------------------------------------*/
PROCESS(packet_generator_process, "Packet Generator");
AUTOSTART_PROCESSES(&packet_generator_process);

PROCESS_THREAD(packet_generator_process, ev, data)
{
  static struct etimer gen_timer;
  PROCESS_BEGIN();
  netstack_sniffer_add(&my_sniffer);
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                    UDP_SERVER_PORT, NULL);
  etimer_set(&gen_timer, poisson_next_delay_ticks());
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&gen_timer));
    /* build and enqueue packet */
	LOG_INFO("toggle_value: %" PRIu32, toggle_value);
	toggle_value++;
	if(is_energy_depleted() || is_simulation_time_over()) {
      //wrapup();
      PROCESS_EXIT();
    }
	send_a_packet(&udp_conn);
    etimer_set(&gen_timer, poisson_next_delay_ticks());
  }
  PROCESS_END();
}

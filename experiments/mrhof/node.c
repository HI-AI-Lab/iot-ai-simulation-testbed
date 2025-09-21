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

#define QUEUE_SIZE 8

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
  uint32_t t_sent;
  uint16_t origin_id;
  char     padding[118];
} app_packet_t;

typedef struct {
  uint32_t tx_count;
  uint32_t rx_count;
  uint32_t generated_count;
  uint32_t forwarded_count;
  uint32_t queue_loss_count;
  double   residual_energy;
  end_reason_t end_reason;
  uint32_t end_time_ms;
  uint32_t ppm;
  unsigned last_parent_id;

  /* queue state */
  app_packet_t queue[QUEUE_SIZE];
  int q_head;
  int q_tail;
  int q_len;
} mote_state_t;

static mote_state_t state = {
  .tx_count = 0,
  .rx_count = 0,
  .generated_count = 0,
  .forwarded_count = 0,
  .queue_loss_count = 0,
  .residual_energy = INIT_ENERGY_J,
  .end_reason = END_NONE,
  .end_time_ms = 0,
  .ppm = (SEND_INTERVAL_MS > 0) ? (60000UL / (unsigned long)SEND_INTERVAL_MS) : 0,
  .last_parent_id = 0,
  .q_head = 0,
  .q_tail = 0,
  .q_len  = 0
};

static int
enqueue_packet(app_packet_t *pkt) {
  if(state.q_len < QUEUE_SIZE) {
    state.queue[state.q_tail] = *pkt;
    state.q_tail = (state.q_tail + 1) % QUEUE_SIZE;
    state.q_len++;
    return 1; /* success */
  } else {
    return 0; /* full */
  }
}

static int
dequeue_packet(app_packet_t *pkt) {
  if(state.q_len > 0) {
    *pkt = state.queue[state.q_head];
    state.q_head = (state.q_head + 1) % QUEUE_SIZE;
    state.q_len--;
    return 1; /* success */
  } else {
    return 0; /* empty */
  }
}

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
			 "Tx=%"PRIu32" Rx=%"PRIu32" Gen=%"PRIu32" Fwd=%"PRIu32" "
			 "QLoss=%"PRIu32" residual=%.6fJ ppm=%"PRIu32" parent=%u\n",
			 node_id,
			 end_reason_str(state.end_reason),
			 (unsigned long)state.end_time_ms,
			 state.tx_count,
			 state.rx_count,
			 state.generated_count,
			 state.forwarded_count,
			 state.queue_loss_count,
			 state.residual_energy,
			 state.ppm,
			 state.last_parent_id);
}

/*MOTE STATE*/

/* === Energy Model (Lei & Liu 2024) === */
/* Distance between two nodes by ID, using generated positions header */
static inline double
distance_nodes(unsigned id1, unsigned id2) {
  double dx = (double)node_pos_x[id1] - (double)node_pos_x[id2];
  double dy = (double)node_pos_y[id1] - (double)node_pos_y[id2];
  return sqrt(dx*dx + dy*dy);
}

/* TX energy (Joules) for one 128B packet to distance d (m) */
static inline double
tx_energy(double d) {
  double dth = sqrt(EPS_FS / EPS_MP);
  if(d <= dth) {
    return PKT_BITS * (E_ELEC + EPS_FS * d * d);
  } else {
    return PKT_BITS * (E_ELEC + EPS_MP * d * d * d * d);
  }
}

/* RX energy (Joules) for one 128B packet — (we’ll use this in the next step for forwarding) */
static inline double
rx_energy(void) {
  return PKT_BITS * E_ELEC;
}
/* === Energy Model (Lei & Liu 2024) === */

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
  if(!dequeue_packet(&pkt)) {
    return; /* queue empty */
  }
  /* transmit it */
  simple_udp_sendto(udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);
  state.tx_count++;
  /* if this packet wasn’t generated locally, count as forwarded */
  if(pkt.origin_id != node_id) {
    state.forwarded_count++;
  }
  /* account TX energy against preferred parent */
  unsigned parent_id = get_parent_id();
  if(parent_id!=-1){
    double d = distance_nodes(node_id, parent_id);
    state.residual_energy -= tx_energy(d);
  }
  state.last_parent_id = parent_id;
}

static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr,
                uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr,
                uint16_t receiver_port,
                const uint8_t *data,
                uint16_t datalen) {
  app_packet_t pkt;
  memcpy(&pkt, data, sizeof(app_packet_t));
  /* account reception */
  state.rx_count++;
  state.residual_energy -= rx_energy();  /* RX energy cost is always paid */
  /* attempt to enqueue for later forwarding */
  if(!enqueue_packet(&pkt)) {
    state.queue_loss_count++;
  }
}

static struct simple_udp_connection udp_conn;

/*---------------------------------------------------------------------------*/
/* Two Processes one for packet generation and one is for queue              */ 
/*---------------------------------------------------------------------------*/
PROCESS(packet_generator_process, "Packet Generator");
PROCESS(queue_handler_process, "Queue Handler");
AUTOSTART_PROCESSES(&packet_generator_process, &queue_handler_process);

PROCESS_THREAD(packet_generator_process, ev, data)
{
  static struct etimer gen_timer;
  PROCESS_BEGIN();
  etimer_set(&gen_timer, poisson_next_delay_ticks());
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&gen_timer));
    /* build and enqueue packet */
    app_packet_t pkt;
    pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
	pkt.origin_id = node_id;
    memset(pkt.padding, 0, sizeof(pkt.padding));
    state.generated_count++;
    if(!enqueue_packet(&pkt)) {
      state.queue_loss_count++;
    }
    etimer_set(&gen_timer, poisson_next_delay_ticks());
  }
  PROCESS_END();
}

PROCESS_THREAD(queue_handler_process, ev, data)
{
  static struct etimer tx_timer;
  PROCESS_BEGIN();
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                      UDP_SERVER_PORT, udp_rx_callback);
  etimer_set(&tx_timer, CLOCK_SECOND / 10); /* push hard: 100ms slot */
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&tx_timer));
    if(is_energy_depleted() || is_simulation_time_over()) {
      wrapup();
      PROCESS_EXIT();
    }
    send_a_packet(&udp_conn);
    etimer_reset(&tx_timer);
  }
  PROCESS_END();
}

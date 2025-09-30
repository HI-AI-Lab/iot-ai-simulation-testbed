#include "contiki-net.h"
#include "contiki.h"
#include "lib/list.h"
#include "math.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include "net/ipv6/uip-ds6.h"
#include "net/link-stats.h"
#include "net/mac/mac.h"
#include "net/packetbuf.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-neighbor.h"   /* <-- needed */
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/routing/routing.h"
#include "net/netstack.h"
#include "net/nbr-table.h"                       /* <-- needed */
#include "node-id.h"
#include "positions-simulation.h"
#include "random.h"
#include "sys/log.h"
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT  8765
#define UDP_SERVER_PORT  5678

#define SIM_END_MS       5000000UL   /* total runtime ms */

/* === Energy Model (Lei & Liu 2024) === */
#define INIT_ENERGY_J   2000.0
#define E_ELEC          50e-9
#define EPS_FS          10e-12
#define EPS_MP          10e-12
#define PKT_BITS        (64*8)
/* ===================================== */

#define MAX_PARENTS_FOR_AGENT 4

/* Raw counters/state (exported for ScriptRunner) */
uint32_t status_gen_count       = 0;
uint32_t status_fwd_count       = 0;
uint32_t status_qloss_count     = 0;
double   status_residual_energy = INIT_ENERGY_J;
uint16_t status_rank            = 0;

/* Derived metrics */
double   status_bdi             = 0.0;
double   status_qlr             = 0.0;
double   status_wr              = 0.0;
uint16_t status_pc              = 0;
uint32_t status_parent_switches = 0;

/* Neighbor candidate table */
uint8_t  status_neighbor_ids[MAX_PARENTS_FOR_AGENT];
uint16_t status_etx_x100[MAX_PARENTS_FOR_AGENT];
uint8_t  status_num_neighbors = 0;

/* Agent handshake */
uint8_t  agent_waiting = 0;    /* 1 while waiting for controller */
uint16_t agent_parent  = 0;    /* controller writes chosen parent ID */

/* === app payload === */
typedef enum {
  END_NONE   = 0,
  END_ENERGY = 1,
  END_TIME   = 2
} end_reason_t;

typedef struct {
  uint32_t t_sent;
  uint16_t origin_id;
  char     padding[58];
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

static const char *end_reason_str(end_reason_t r) {
  switch(r) {
    case END_ENERGY: return "energy";
    case END_TIME:   return "time";
    default:         return "none";
  }
}

static void wrapup(void) {
  LOG_INFO("WRAPUP node_id=%u reason=%s end_ms=%" PRIu32 " "
           "Gen=%" PRIu32 " Fwd=%" PRIu32 " QLoss=%" PRIu32 " qsize=%" PRIu32 " "
           "residual=%.6fJ ppm=%" PRIu32 " parent=%u switches=%" PRIu32 "\n",
           (unsigned)node_id,
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

/* ===== helpers ===== */
static inline double distance_nodes(unsigned id1, unsigned id2) {
  double dx = (double)node_pos_x[id1] - (double)node_pos_x[id2];
  double dy = (double)node_pos_y[id1] - (double)node_pos_y[id2];
  return sqrt(dx*dx + dy*dy);
}

static inline double tx_energy(double d, uint32_t bits) {
  double dth = sqrt(EPS_FS / EPS_MP);
  if(d <= dth) {
    return (double)bits * (E_ELEC + EPS_FS * d * d);
  } else {
    return (double)bits * (E_ELEC + EPS_MP * d * d * d * d);
  }
}

static inline double rx_energy(uint32_t bits) {
  return (double)bits * E_ELEC;
}

static clock_time_t poisson_next_delay_ticks(void) {
  float u = ((float)random_rand() + 1.0f) / ((float)RANDOM_RAND_MAX + 1.0f);
  float mean_sec = (state.ppm > 0) ? (60.0f / (float)state.ppm) : 1.0f;
  float x_sec = -mean_sec * logf(u);
  clock_time_t ticks = (clock_time_t)(x_sec * (float)CLOCK_SECOND);
  if(ticks < 1) ticks = 1;
  return ticks;
}

static unsigned ip_to_nodeid(const uip_ipaddr_t *ip) {
  return (unsigned)UIP_HTONS(ip->u16[7]);
}

static unsigned get_parent_id(void) {
  rpl_dag_t *dag = rpl_get_any_dag();
  if(dag && dag->preferred_parent) {
    const uip_ipaddr_t *p_ip = rpl_parent_get_ipaddr(dag->preferred_parent);
    return ip_to_nodeid(p_ip);
  }
  return (unsigned)-1;
}

static int is_simulation_time_over(void) {
  uint32_t now_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
  if(now_ms >= SIM_END_MS) {
    state.end_reason = END_TIME;
    state.end_time_ms = now_ms;
    return 1;
  }
  return 0;
}

static int is_energy_depleted(void) {
  if(state.residual_energy <= 0) {
    state.residual_energy = 0;
    state.end_reason = END_ENERGY;
    state.end_time_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
    return 1;
  }
  return 0;
}

/* ===== app I/O ===== */
static void send_a_packet(struct simple_udp_connection *udp_conn) {
  uip_ipaddr_t dest_ipaddr;
  if(!NETSTACK_ROUTING.node_is_reachable() ||
     !NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {
    return;
  }

  /* --- Enforce controller's parent before each packet --- */
  if(agent_parent != 0) {
    rpl_dag_t *dag = rpl_get_any_dag();
    if(dag) {
      rpl_nbr_t *nbr;
      for(nbr = nbr_table_head(rpl_neighbors);
          nbr != NULL;
          nbr = nbr_table_next(rpl_neighbors, nbr)) {
        uip_ipaddr_t *ip = rpl_neighbor_get_ipaddr(nbr);
        if(ip && ip_to_nodeid(ip) == agent_parent) {
          if(dag->preferred_parent != nbr) {
            rpl_neighbor_set_preferred_parent(nbr);
			if(state.last_parent_id != agent_parent) state.parent_switches++;
            state.last_parent_id = agent_parent;
          }
          break;
        }
      }
    }
  }

  /* --- Normal packet generation --- */
  app_packet_t pkt;
  pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
  pkt.origin_id = node_id;
  memset(pkt.padding, 0, sizeof(pkt.padding));
  simple_udp_sendto(udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);
  state.gen_count++;
}


static void sniff_input(void) {
  uint16_t len = packetbuf_datalen();
  state.residual_energy -= rx_energy(len * 8);
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
      state.residual_energy -= tx_energy(d, len * 8);
    }
  }
}

/* ===== neighbor features ===== */

static void refresh_etx_table(void) {
  
  status_num_neighbors = 0;
  rpl_nbr_t *nbr;
  
  for(nbr = nbr_table_head(rpl_neighbors);
      nbr != NULL;
      nbr = nbr_table_next(rpl_neighbors, nbr)) {
    
    if(status_num_neighbors >= MAX_PARENTS_FOR_AGENT) break;

    uip_ipaddr_t *p_ip = rpl_neighbor_get_ipaddr(nbr);
    if(!p_ip) continue;
    
    status_neighbor_ids[status_num_neighbors] = (uint8_t)ip_to_nodeid(p_ip);
	
	//uint16_t etx_x100_for_neighbor = 1000;
	//const struct link_stats *st = rpl_neighbor_get_link_stats(nbr);
    //if(st != NULL) etx_x100_for_neighbor = ((100UL * st->etx) / LINK_STATS_ETX_DIVISOR);
	//status_etx_x100[status_num_neighbors] = etx_x100_for_neighbor;
    status_num_neighbors++;
  }
}

static void refresh_status(void) {
  status_parent_switches = state.parent_switches;
  status_gen_count       = state.gen_count;
  status_fwd_count       = state.fwd_count;
  status_qloss_count     = state.q_loss_count;
  status_residual_energy = state.residual_energy;

  rpl_dag_t *dag = rpl_get_any_dag();
  status_rank = dag ? dag->rank : 0;

  status_bdi = 1.0 - (status_residual_energy / INIT_ENERGY_J);
  status_qlr = (status_fwd_count > 0) ?
               ((double)status_qloss_count / status_fwd_count) : 0.0;
  status_wr  = (status_gen_count > 0) ?
               ((double)status_fwd_count / status_gen_count) : 0.0;
  
  /* Parent count = neighbors in table */
  status_pc = 0;
  {
    rpl_nbr_t *nbr;
    for(nbr = nbr_table_head(rpl_neighbors);
        nbr != NULL;
        nbr = nbr_table_next(rpl_neighbors, nbr)) {
      status_pc++;
    }
  }
  refresh_etx_table();
}

/* ===== sniffer & UDP ===== */
NETSTACK_SNIFFER(my_sniffer, sniff_input, sniff_output);
static struct simple_udp_connection udp_conn;

/* ===== processes ===== */
PROCESS(packet_generator_process, "Packet Generator");
PROCESS(status_refresher_process, "Status Refresher");
AUTOSTART_PROCESSES(&packet_generator_process, &status_refresher_process);

PROCESS_THREAD(packet_generator_process, ev, data)
{
  static struct etimer gen_timer;
  PROCESS_BEGIN();
  netstack_sniffer_add(&my_sniffer);
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL, UDP_SERVER_PORT, NULL);
  etimer_set(&gen_timer, poisson_next_delay_ticks());
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&gen_timer));
    if(is_energy_depleted() || is_simulation_time_over()) {
      wrapup();
      PROCESS_EXIT();
    }
	if(agent_parent != 0) send_a_packet(&udp_conn);
    etimer_set(&gen_timer, poisson_next_delay_ticks());
  }
  PROCESS_END();
}

PROCESS_THREAD(status_refresher_process, ev, data)
{
  static struct etimer t;
  static uint32_t sec_counter = 0;
  PROCESS_BEGIN();
  etimer_set(&t, CLOCK_SECOND);
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&t));
    sec_counter++;
	//if(sec_counter % 10 == 9)
	refresh_status();
    etimer_reset(&t);
  }
  PROCESS_END();
}

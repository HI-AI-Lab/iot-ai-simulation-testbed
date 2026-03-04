#include "contiki-net.h"
#include "contiki.h"
#include "lib/list.h"
#include "math.h"
#include "net/queuebuf.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6-nbr.h"
#include "net/ipv6/uip-ds6.h"
#include "net/ipv6/uip-ds6-route.h"
#include "net/link-stats.h"
#include "net/mac/mac.h"
#include "net/packetbuf.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-neighbor.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/routing/routing.h"
#include "net/netstack.h"
#include "net/nbr-table.h"
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

/* ==== RPL constants ==== */
#ifndef MRHOF_ETX_DIVISOR
#define MRHOF_ETX_DIVISOR 128
#endif
#ifndef RPL_ROOT_RANK
#define RPL_ROOT_RANK 256
#endif
#ifndef RPL_MIN_HOPRANKINC
#define RPL_MIN_HOPRANKINC 256
#endif

/* ==== Simulation ==== */
#define UDP_CLIENT_PORT  8765
#define UDP_SERVER_PORT  5678
#define SIM_END_MS       5000000UL

/* ==== Energy Model ==== */
#define INIT_ENERGY_J   2000.0
#define E_ELEC          50e-9
#define EPS_FS          10e-12
#define EPS_MP          10e-12
#define PKT_BITS       (64*8)

/* ==== Parent table ==== */
#define MAX_PARENTS_FOR_AGENT 4

/* ============================================================
 *  STATUS EXPORT VARIABLES (READ BY CONTROLLER)
 *  DO NOT TOUCH NAMES (your scripts depend on these)
 * ============================================================*/

/* raw counters */
uint32_t status_gen_count       = 0;
uint32_t status_fwd_count       = 0;
uint32_t status_qloss_count     = 0;
double   status_residual_energy = INIT_ENERGY_J;
uint16_t status_rank            = 0;

/* derived metrics */
double   status_bdi             = 0.0;
double   status_qlr             = 0.0;
double   status_wr              = 0.0;
uint16_t status_pc              = 0;
uint32_t status_parent_switches = 0;

/* new metrics */
double   status_si              = 0.0;   /* Stability Index */
double   status_tv              = 0.0;   /* Trust Value */
double   status_str             = 0.0;   /* Stretch of Rank */
uint32_t status_qo              = 0;     /* Queue occupancy snapshot */
uint8_t  status_global_stop     = 0;     /* set by simulation.js to stop all nodes */

/* PFI counters */
static uint32_t parent_tx_ok[256];
static uint32_t parent_tx_attempts[256];
double   status_pfi[MAX_PARENTS_FOR_AGENT];

/* neighbor candidates */
uint8_t  status_neighbor_ids[MAX_PARENTS_FOR_AGENT];
uint16_t status_etx_x100[MAX_PARENTS_FOR_AGENT];
int16_t  status_link_rssi_dbm[MAX_PARENTS_FOR_AGENT];
uint8_t  status_num_neighbors = 0;

static uint8_t mote_dead = 0;
static uint8_t wrapup_done = 0;

/* agent control */
uint8_t  agent_waiting = 0;
uint16_t agent_parent  = 0;

static uint8_t ever_joined_dodag = 0;
static uint8_t nn_max = 0;

/* ============================================================
 * APP PACKET
 * ============================================================*/
typedef enum {
  END_NONE   = 0,
  END_ENERGY = 1,
  END_TIME   = 2,
  END_GLOBAL = 3
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
  .ppm = (SEND_INTERVAL_MS > 0) ?
         (60000UL / (unsigned long)SEND_INTERVAL_MS) : 0,
  .last_parent_id = 0,
  .parent_switches = 0
};

/* ============================================================
 * ENERGY HELPERS
 * ============================================================*/
static inline double distance_nodes(unsigned id1, unsigned id2) {
  double dx = node_pos_x[id1] - node_pos_x[id2];
  double dy = node_pos_y[id1] - node_pos_y[id2];
  return sqrt(dx*dx + dy*dy);
}

static inline double tx_energy(double d, uint32_t bits) {
  double dth = sqrt(EPS_FS / EPS_MP);
  return (bits * (E_ELEC + (d <= dth ? EPS_FS*d*d : EPS_MP*d*d*d*d)));
}

static inline double rx_energy(uint32_t bits) {
  return bits * E_ELEC;
}

static inline void consume_energy(double dj) {
  if(mote_dead) return;

  state.residual_energy -= dj;

  if(state.residual_energy <= 0) {
    state.residual_energy = 0;

    /* record EXACT depletion time ONCE */
    if(state.end_reason == END_NONE) {
      state.end_reason = END_ENERGY;
      state.end_time_ms = (uint32_t)(clock_time()*1000UL/CLOCK_SECOND);
    }

    mote_dead = 1;

    /* stop forwarding / participation */
    NETSTACK_MAC.off();
    NETSTACK_RADIO.off();
  }
}

static inline void apply_global_stop_if_needed(void) {
  if(mote_dead || !status_global_stop) return;

  if(state.end_reason == END_NONE) {
    state.end_reason = END_GLOBAL;
    state.end_time_ms = (uint32_t)(clock_time()*1000UL/CLOCK_SECOND);
  }

  mote_dead = 1;
  NETSTACK_MAC.off();
  NETSTACK_RADIO.off();
}

/* ============================================================
 * PARENT PINNING (unchanged)
 * ============================================================*/
static unsigned ip_to_nodeid(const uip_ipaddr_t *ip) {
  return (unsigned)UIP_HTONS(ip->u16[7]);
}

static unsigned get_parent_id(void) {
  rpl_dag_t *dag = rpl_get_any_dag();
  if(dag && dag->preferred_parent) {
    return ip_to_nodeid(rpl_parent_get_ipaddr(dag->preferred_parent));
  }
  return (unsigned)-1;
}


//static void enforce_agent_parent_if_needed(void){}

static void pin_route_to_root_via(const uip_ipaddr_t *nh)
{
  if(!nh) return;
  uip_ipaddr_t root;
  if(!NETSTACK_ROUTING.get_root_ipaddr(&root)) return;

  uip_ds6_route_t *r = uip_ds6_route_lookup(&root);
  if(r) {
    if(uip_ipaddr_cmp(uip_ds6_route_nexthop(r), nh)) return;
    uip_ds6_route_rm(r);
  }
  (void)uip_ds6_route_add(&root, 128, nh);
}

static void enforce_agent_parent_if_needed(void) {
  if(agent_parent == 0) return;

  rpl_dag_t *dag = rpl_get_any_dag();
  if(!dag) return;

  if(dag->preferred_parent) {
    const uip_ipaddr_t *curr = rpl_parent_get_ipaddr(dag->preferred_parent);
    if(curr && ip_to_nodeid(curr) == agent_parent) {
      pin_route_to_root_via(curr);
      return;
    }
  }

  for(rpl_nbr_t *nbr = nbr_table_head(rpl_neighbors);
      nbr != NULL; nbr = nbr_table_next(rpl_neighbors, nbr)) {

    const uip_ipaddr_t *ip = rpl_neighbor_get_ipaddr(nbr);
    if(ip && ip_to_nodeid(ip) == agent_parent) {

      rpl_neighbor_set_preferred_parent(nbr);
      pin_route_to_root_via(ip);

      if(state.last_parent_id != agent_parent) {
        state.parent_switches++;
        state.last_parent_id = agent_parent;
      }
      return;
    }
  }
}


/* ============================================================
 * PACKET SNIFFERS — QLR, ENERGY, PFI
 * ============================================================*/
static void sniff_input(void) {
  apply_global_stop_if_needed();
  if(mote_dead) return;
  uint16_t len = packetbuf_datalen();
  if(len) consume_energy(rx_energy(len*8));
}

static void sniff_output(int mac_status) {
  apply_global_stop_if_needed();
  if(mote_dead) return;
  enforce_agent_parent_if_needed();
  uint16_t len = packetbuf_datalen();
  unsigned parent_id = get_parent_id();

  if(parent_id < 256) parent_tx_attempts[parent_id]++;

  double d = 0.0;
  if(parent_id != (unsigned)-1)
    d = distance_nodes(node_id, parent_id);

  switch(mac_status) {
    case MAC_TX_QUEUE_FULL:
      state.q_loss_count++;
      return;

    case MAC_TX_OK:
      if(parent_id < 256) parent_tx_ok[parent_id]++;
      if(len) consume_energy(tx_energy(d, len*8));
      state.fwd_count++;
      return;

    default:
      if(len) consume_energy(tx_energy(d, len*8));
      return;
  }
}

/* ============================================================
 * TOP-K NEIGHBORS BY ETX
 * ============================================================*/
static void topk_insert(uint8_t *ids, uint16_t *etx, int16_t *rssi,
                        uint8_t *k, uint8_t K,
                        uint8_t nid, uint16_t ex, int16_t r)
{
  uint8_t pos = 0;
  while(pos < *k) {
    if(ex < etx[pos] || (ex == etx[pos] && nid < ids[pos])) break;
    pos++;
  }
  if(*k < K) {
    for(uint8_t j = *k; j > pos; j--) {
      ids[j]=ids[j-1]; etx[j]=etx[j-1]; rssi[j]=rssi[j-1];
    }
    ids[pos]=nid; etx[pos]=ex; rssi[pos]=r; (*k)++;
  } else if(pos < K) {
    for(uint8_t j = K-1; j > pos; j--) {
      ids[j]=ids[j-1]; etx[j]=etx[j-1]; rssi[j]=rssi[j-1];
    }
    ids[pos]=nid; etx[pos]=ex; rssi[pos]=r;
  }
}

static void refresh_etx_table(void) {
  status_num_neighbors = 0;

  uint8_t  tids[MAX_PARENTS_FOR_AGENT] = {0};
  uint16_t tcost[MAX_PARENTS_FOR_AGENT] = {0};  /* path-cost proxy x100 */
  int16_t  trssi[MAX_PARENTS_FOR_AGENT] = {0};
  uint8_t  k = 0;

  rpl_dag_t *dag = rpl_get_any_dag();
  if(!dag) goto clear;

  for(rpl_nbr_t *nbr = nbr_table_head(rpl_neighbors);
      nbr != NULL;
      nbr = nbr_table_next(rpl_neighbors, nbr)) {

    if(!rpl_neighbor_is_fresh(nbr)) continue;
    if(!rpl_neighbor_is_acceptable_parent(nbr)) continue;

    const uip_ipaddr_t *ip = rpl_neighbor_get_ipaddr(nbr);
    if(!ip) continue;

    const struct link_stats *st = rpl_neighbor_get_link_stats(nbr);
    if(!st) continue;

    /* ---------- LINK ETX (x100) ---------- */
    uint32_t link_x100 =
      (uint32_t)((st->etx * 100UL + (MRHOF_ETX_DIVISOR/2)) / MRHOF_ETX_DIVISOR);

    if(link_x100 == 0 || link_x100 > 0xFFFF) link_x100 = 0xFFFF;

    /* ---------- rank_via -> hop_via ---------- */
    uint16_t rank_via = rpl_neighbor_rank_via_nbr(nbr);
    if(rank_via == 0 || rank_via == RPL_INFINITE_RANK || rank_via < RPL_ROOT_RANK) {
      continue; /* no valid route via this neighbor */
    }

    uint32_t hop_via =
      (uint32_t)((rank_via - RPL_ROOT_RANK) / RPL_MIN_HOPRANKINC);

    /* ---------- PATH COST PROXY (x100) ---------- */
    uint32_t path_x100 = link_x100 + hop_via * 100UL;
    if(path_x100 > 0xFFFF) path_x100 = 0xFFFF;

    int16_t rssi = st->rssi;

    topk_insert(tids, tcost, trssi, &k, MAX_PARENTS_FOR_AGENT,
                (uint8_t)ip_to_nodeid(ip), (uint16_t)path_x100, rssi);
  }

clear:
  for(uint8_t i = 0; i < MAX_PARENTS_FOR_AGENT; i++) {
    status_neighbor_ids[i]  = (i < k) ? tids[i]  : 0;
    status_etx_x100[i]      = (i < k) ? tcost[i] : 0;  /* NOW: path-cost proxy */
    status_link_rssi_dbm[i] = (i < k) ? trssi[i] : 0;
  }

  status_num_neighbors = k;
  if(status_num_neighbors > nn_max) nn_max = status_num_neighbors;
}

/* ============================================================
 * STATUS REFRESH — ALL METRICS UPDATED HERE
 * (LOGGING REMAINS UNCHANGED)
 * ============================================================*/
static void refresh_status(void)
{
  /* raw */
  status_gen_count       = state.gen_count;
  status_fwd_count       = state.fwd_count;
  status_qloss_count     = state.q_loss_count;
  status_residual_energy = state.residual_energy;

  rpl_dag_t *dag = rpl_get_any_dag();
  status_rank = dag ? dag->rank : 0;
  
  if(dag && status_rank != 0 && status_rank != RPL_INFINITE_RANK) {
	ever_joined_dodag = 1;
  }

  /* QO — queue occupancy snapshot: used = TOTAL - FREE */
  status_qo = (uint32_t)(QUEUEBUF_CONF_NUM - queuebuf_numfree());


  /* BDI */
  status_bdi = 1.0 - (status_residual_energy / INIT_ENERGY_J);
  if(status_bdi < 0) status_bdi=0;
  if(status_bdi > 1) status_bdi=1;

  /* QLR */
  uint32_t attempts = status_fwd_count + status_qloss_count;
  status_qlr = (attempts>0) ?
               (double)status_qloss_count / attempts : 0.0;

  /* WR */
  uint8_t p = get_parent_id();
  uint32_t a = 0, s = 0;

  if(p < 256) {
    a = parent_tx_attempts[p];
    s = parent_tx_ok[p];
  }

  status_wr = (a > 0) ? ((double)s / a) : 0.0;

  /* PC — number of children */
  status_pc = 0;
  {
    uip_ds6_route_t *r = uip_ds6_route_head();
    while(r) {
      const uip_ipaddr_t *nh = uip_ds6_route_nexthop(r);
      if(nh && ip_to_nodeid(nh) == node_id) {
        status_pc++;
      }
      r = uip_ds6_route_next(r);
    }
  }

  /* SI — stability */
  status_si = 1.0 / (1.0 + (double)state.parent_switches);

  /* TV — trust */
  uint32_t total = status_fwd_count + status_qloss_count;
  status_tv = (total>0) ?
              (double)status_fwd_count / total : 1.0;

  /* STR — stretch of rank */
  status_str = (status_rank >= RPL_ROOT_RANK) ?
               (double)(status_rank - RPL_ROOT_RANK) : 0;

  /* FIRST: rebuild parent table so status_neighbor_ids[] is current */
  refresh_etx_table();

  /* THEN: PFI — per top-K parent for current neighbors */
  for(uint8_t i=0; i<MAX_PARENTS_FOR_AGENT; i++){
    uint8_t cand = status_neighbor_ids[i];

    uint32_t aa = 0;
    uint32_t ss = 0;

    if(cand < 256) {
      aa = parent_tx_attempts[cand];
      ss = parent_tx_ok[cand];
    }

    status_pfi[i] = (aa > 0) ? ((double)ss / aa) : 0.0;
  }
}


/* ============================================================
 * APP I/O
 * ============================================================*/
static void send_a_packet(struct simple_udp_connection *udp_conn) {
  apply_global_stop_if_needed();
  if(mote_dead) return;	
	
  uip_ipaddr_t dest_ipaddr;

  if(!NETSTACK_ROUTING.node_is_reachable() ||
     !NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr))
    return;

  enforce_agent_parent_if_needed();

  app_packet_t pkt;
  pkt.t_sent   = (uint32_t)(clock_time()*1000UL/CLOCK_SECOND);
  pkt.origin_id= node_id;
  memset(pkt.padding,0,sizeof(pkt.padding));

  simple_udp_sendto(udp_conn,&pkt,sizeof(pkt),&dest_ipaddr);
  state.gen_count++;
}

/* ============================================================
 * WRAPUP (unchanged)
 * ============================================================*/
static const char *end_reason_str(end_reason_t r) {
  switch(r) {
    case END_ENERGY: return "energy";
    case END_TIME:   return "time";
    case END_GLOBAL: return "global_stop";
    default:         return "none";
  }
}

static void wrapup(void) {
	if(wrapup_done) return;
	wrapup_done = 1;
	LOG_INFO("WRAPUP node_id=%u reason=%s end_ms=%"PRIu32" "
         "Gen=%"PRIu32" Fwd=%"PRIu32" QLoss=%"PRIu32" qsize=%"PRIu32" "
         "residual=%.6fJ ppm=%"PRIu32" parent=%u switches=%"PRIu32" ever_joined_dodag=%"PRIu32" nn_max=%"PRIu32"\n",
         node_id,
         end_reason_str(state.end_reason),
         state.end_time_ms,
         state.gen_count,
         state.fwd_count,
         state.q_loss_count,
         state.qsize,
         state.residual_energy,
         state.ppm,
         state.last_parent_id,
         state.parent_switches,
		 ever_joined_dodag,
         nn_max);
}

/* ============================================================
 * TERMINATION CHECKS
 * ============================================================*/
static int is_simulation_time_over(void) {
  uint32_t now = (uint32_t)(clock_time()*1000UL/CLOCK_SECOND);
  if(now >= SIM_END_MS) {
    state.end_reason = END_TIME;
    state.end_time_ms = now;
    return 1;
  }
  return 0;
}

/* Exponential inter-arrival based on SEND_INTERVAL_MS mean */
static clock_time_t exp_interval(void) {
  double u = (double)random_rand() / RANDOM_RAND_MAX;
  if(u <= 1e-9) u = 1e-9;   /* avoid log(0) */
  double interval_ms = -SEND_INTERVAL_MS * log(u);  /* exponential */
  return (clock_time_t)(interval_ms * CLOCK_SECOND / 1000.0);
}

/* ============================================================
 * PROCESSES
 * ============================================================*/
NETSTACK_SNIFFER(my_sniffer, sniff_input, sniff_output);
static struct simple_udp_connection udp_conn;

PROCESS(packet_generator_process, "Packet Generator");
PROCESS(status_refresher_process, "Status Refresher");

AUTOSTART_PROCESSES(&packet_generator_process,
                    &status_refresher_process);

/* generator */
PROCESS_THREAD(packet_generator_process, ev, data)
{
  static struct etimer gen_timer;

  PROCESS_BEGIN();

  netstack_sniffer_add(&my_sniffer);

  simple_udp_register(&udp_conn,
                      UDP_CLIENT_PORT,
                      NULL,
                      UDP_SERVER_PORT,
                      NULL);

  if(state.ppm == 0) state.ppm = 1;

  /* FIRST PACKET: exponential interval */
  etimer_set(&gen_timer, exp_interval());

  while(1) {
    apply_global_stop_if_needed();

    /* If dead, exit immediately (do not wait for timer) */
    if(mote_dead) {
      wrapup();
      PROCESS_EXIT();
    }

    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&gen_timer));

    apply_global_stop_if_needed();

    /* termination checks after wake-up */
    if(mote_dead || is_simulation_time_over()) {
      wrapup();
      PROCESS_EXIT();
    }

    send_a_packet(&udp_conn);

    /* NEXT PACKET: exponential interval */
    etimer_set(&gen_timer, exp_interval());
  }

  PROCESS_END();
}


/* metrics refresh */
PROCESS_THREAD(status_refresher_process, ev, data)
{
  static struct etimer t;

  PROCESS_BEGIN();

  etimer_set(&t, CLOCK_SECOND);

  while(1) {
    apply_global_stop_if_needed();

    /* If dead, exit immediately */
    if(mote_dead) {
      wrapup();
      PROCESS_EXIT();
    }

    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&t));

    apply_global_stop_if_needed();

    /* Death can occur while waiting; re-check before doing work */
    if(mote_dead) {
      wrapup();
      PROCESS_EXIT();
    }

    refresh_status();
    enforce_agent_parent_if_needed();
    etimer_reset(&t);
  }

  PROCESS_END();
}

#include "contiki.h"
#include "contiki-net.h"
#include "net/routing/routing.h"
#include "net/ipv6/simple-udp.h"
#include "net/ipv6/uip-ds6.h"
#include "net/routing/rpl-lite/rpl.h"
#include "sys/node-id.h"
#include "sys/log.h"
#include <string.h>
#include <stdint.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ------------------- App config ------------------- */
#define UDP_PORT 8765
#define MAX_NODE_ID 1024

/* Helper: milliseconds from Contiki clock */
static inline uint32_t now_ms(void) {
  return (uint32_t)((uint64_t)clock_time() * 1000ULL / CLOCK_SECOND);
}

/* ------------------- UDP ------------------- */
static struct simple_udp_connection udp_conn;

/* TX payload (packed to avoid padding differences) */
typedef struct __attribute__((packed)) {
  uint16_t src_id;
  uint32_t seq;
  uint32_t t_tx_ms;   /* sender timestamp (for E2E @ root) */
} payload_t;

static uint32_t tx_seq = 0;

/* ------------------- PRR/E2E counters @ root ------------------- */
static uint32_t rcvd_per_node[MAX_NODE_ID];
static uint32_t last_seq_per_node[MAX_NODE_ID];
static uint32_t expected_per_node[MAX_NODE_ID];

/* ------------------- RPL callbacks (debug proof) ------------------- */
static void on_parent_switch(uip_ipaddr_t *addr) {
  LOG_INFO("RPL PARENT-SWITCH -> "); LOG_INFO_6ADDR(addr); LOG_INFO_("\n");
}
static void on_new_dag_version(uint8_t ver) {
  LOG_INFO("RPL NEW-DAG-VERSION %u\n", ver);
}
static rpl_callbacks_t rpl_cbs = {
  .parent_switch = on_parent_switch,
  .new_dag_version = on_new_dag_version
};

/* ------------------- Root: UDP RX ------------------- */
static void udp_rx_cb(struct simple_udp_connection *c,
                      const uip_ipaddr_t *sender_addr, uint16_t sender_port,
                      const uip_ipaddr_t *receiver_addr, uint16_t receiver_port,
                      const uint8_t *data, uint16_t datalen)
{
  if(datalen < sizeof(payload_t)) return;

  payload_t p;
  memcpy(&p, data, sizeof(p));

  /* Bounds-guard the arrays */
  uint16_t id = p.src_id;
  if(id >= MAX_NODE_ID) return;

  rcvd_per_node[id]++;

  /* expected_per_node tracks "sent" count inferred from seq jumps */
  if(p.seq > last_seq_per_node[id]) {
    expected_per_node[id] += (p.seq - last_seq_per_node[id]);
    last_seq_per_node[id] = p.seq;
  }

  uint32_t t_rx = now_ms();
  uint32_t e2e  = t_rx - p.t_tx_ms;

  LOG_INFO("DEBUG RECV id=%u seq=%lu E2E=%lums\n",
           (unsigned)id, (unsigned long)p.seq, (unsigned long)e2e);

  static uint32_t recv_count = 0;
  if((++recv_count % 50) == 0) {
    uint64_t sum_rcv = 0, sum_exp = 0;
    for(unsigned i = 0; i < MAX_NODE_ID; i++) {
      sum_rcv += rcvd_per_node[i];
      sum_exp += expected_per_node[i];
    }
    if(sum_exp == 0) sum_exp = 1; /* avoid div-by-zero on early boot */
    double prr = (double)sum_rcv / (double)sum_exp;
    LOG_INFO("PRR_GLOBAL %.3f (%lu/%lu)\n",
             prr, (unsigned long)sum_rcv, (unsigned long)sum_exp);
  }
}

/* ------------------- Set root global /64 and start RPL ------------------- */
static void set_root_global_addr_and_start(void) {
  uip_ipaddr_t ip;
  /* Use a well-known lab prefix; change if you want */
  uip_ip6addr(&ip, 0x2001,0xdb8,0,0,0,0,0,0);
  /* Autoconfigure IID from MAC */
  uip_ds6_set_addr_iid(&ip, &uip_lladdr);
  uip_ds6_addr_add(&ip, 0, ADDR_AUTOCONF);

  LOG_INFO("ROOT GLOBAL "); LOG_INFO_6ADDR(&ip); LOG_INFO_("\n");

  NETSTACK_ROUTING.root_start(); /* advertise prefix, start DODAG */
  LOG_INFO("ROOT-START done\n");
}

/* ------------------- Periodic reachability/health log ------------------- */
static void health_log(void) {
  int reachable = NETSTACK_ROUTING.node_is_reachable();
  /* Rank 0xffff means "infinite" (not joined) */
  uint16_t my_rank = rpl_get_my_rank();

  LOG_INFO("REACH=%d RANK=%u\n", reachable, my_rank);

  if(node_id != 1 && !reachable) {
    LOG_INFO("HINT: sending DIS to discover DAG\n");
    rpl_icmp6_dis_output(NULL);
  }
}

/* ------------------- Periodic UDP sender (non-root) ------------------- */
static void try_send_to_root(void) {
  if(node_id == 1) return;

  uip_ipaddr_t root_ip;
  if(NETSTACK_ROUTING.node_is_reachable() &&
     NETSTACK_ROUTING.get_root_ipaddr(&root_ip)) {

    payload_t p;
    p.src_id = (uint16_t)node_id;
    p.seq    = tx_seq++;
    p.t_tx_ms= now_ms();

    simple_udp_sendto(&udp_conn, &p, sizeof(p), &root_ip);
    LOG_INFO("DEBUG SEND id=%u seq=%lu\n", node_id, (unsigned long)p.seq);

  } else {
    LOG_INFO("SEND-SKIP (unreachable)\n");
    /* Nudge discovery if still not in DAG */
    rpl_icmp6_dis_output(NULL);
  }
}

/* ------------------- Main process ------------------- */
PROCESS(app_process, "RPL-OF0 UDP app");
AUTOSTART_PROCESSES(&app_process);

PROCESS_THREAD(app_process, ev, data)
{
  static struct etimer et_send;
  static struct etimer et_health;

  PROCESS_BEGIN();

  /* RPL debug callbacks */
  rpl_set_callback(&rpl_cbs);

  /* UDP sockets:
   * - Root registers RX callback.
   * - Non-root may register without cb (harmless to share one conn).
   */
  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT,
                      (node_id == 1) ? udp_rx_cb : NULL);

  if(node_id == 1) {
    /* Important: set global address BEFORE root_start() */
    set_root_global_addr_and_start();
  } else {
    LOG_INFO("NODE START (id=%u)\n", node_id);
  }

  /* Stagger startup a little */
  etimer_set(&et_health, CLOCK_SECOND * 5);
  etimer_set(&et_send,   (clock_time_t)(SEND_INTERVAL_SEC * CLOCK_SECOND));

  while(1) {
    PROCESS_YIELD();

    if(etimer_expired(&et_health)) {
      health_log();
      etimer_reset(&et_health);
    }

    if(etimer_expired(&et_send)) {
      try_send_to_root();
      etimer_reset(&et_send);
    }
  }

  PROCESS_END();
}

#include "contiki.h"
#include "net/netstack.h"
#include "net/routing/routing.h"
#include "net/ipv6/uip-ds6.h"
#include "net/ipv6/simple-udp.h"
#include "sys/node-id.h"
#include "sys/log.h"
#include "sys/energest.h"
#include "lib/random.h"
#include <stdio.h>
#include <string.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

#define PAYLOAD_SIZE 128
#define REPORT_INTERVAL (10 * CLOCK_SECOND)
#define TX_BASE_INTERVAL CLOCK_SECOND
#define UDP_PORT 12345

PROCESS(app_process, "OF0 Packet Sender (RPL Lite)");
AUTOSTART_PROCESSES(&app_process);

static struct simple_udp_connection udp_conn;
static struct etimer et_tx, et_report;
static uint16_t seq_id = 0;
static uint16_t generated = 0, received = 0, dropped = 0;
static unsigned long cpu = 0, tx = 0, rx = 0;
static float total_energy_mJ = 0;
static uint8_t is_dead = 0;

/* ------------ Simulate Packet Drop ------------------------ */
static int simulate_queue_full(void) {
  return (random_rand() % 10) < 2; // 20% drop probability
}

/* ------------ RECV Logging Handler ------------------------ */
static void recv_callback(struct simple_udp_connection *c,
                          const uip_ipaddr_t *sender_addr,
                          uint16_t sender_port,
                          const uip_ipaddr_t *receiver_addr,
                          uint16_t receiver_port,
                          const uint8_t *data,
                          uint16_t datalen) {
  LOG_INFO("RECV %u %lu %.*s\n", node_id, clock_time(), datalen, (char *)data);
  received++;
}

PROCESS_THREAD(app_process, ev, data)
{
  static uip_ipaddr_t dest_ipaddr;

  PROCESS_BEGIN();

  if(node_id == 1) {
    NETSTACK_ROUTING.root_start(); // ✅ Set this node as RPL root at runtime
  }

  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, recv_callback);
  etimer_set(&et_tx, TX_BASE_INTERVAL);
  etimer_set(&et_report, REPORT_INTERVAL);

  while(1) {
    PROCESS_WAIT_EVENT();

    /* ---------- Send Packet ---------- */
    if(etimer_expired(&et_tx)) {
      etimer_reset(&et_tx);

      if(NETSTACK_ROUTING.node_is_reachable() &&
         NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {

        LOG_INFO("SEND %u %lu %u\n", node_id, clock_time(), seq_id);

        char payload[PAYLOAD_SIZE];
        int len = snprintf(payload, sizeof(payload), "%u-%lu-%u", node_id, clock_time(), seq_id);
        memset(payload + len, 'X', PAYLOAD_SIZE - len - 1);
        payload[PAYLOAD_SIZE - 1] = '\0';

        if(simulate_queue_full()) {
          dropped++;
        } else {
          simple_udp_sendto(&udp_conn, payload, sizeof(payload), &dest_ipaddr);
        }

        generated++;
        seq_id++;
      }
    }

    /* ---------- Report QLR and NLT ---------- */
    if(etimer_expired(&et_report)) {
      etimer_reset(&et_report);

      /* QLR Logging */
      uint16_t total_in = generated + received;
      float qlr = (float)dropped / (float)(total_in == 0 ? 1 : total_in);
      LOG_INFO("QLR %u %u %u %u %.3f\n", node_id, generated, received, dropped, qlr);

      /* Energy Accounting for NLT */
      energest_flush();
      unsigned long cpu_now = energest_type_time(ENERGEST_TYPE_CPU);
      unsigned long tx_now  = energest_type_time(ENERGEST_TYPE_TRANSMIT);
      unsigned long rx_now  = energest_type_time(ENERGEST_TYPE_LISTEN);

      unsigned long cpu_diff = cpu_now - cpu;
      unsigned long tx_diff  = tx_now - tx;
      unsigned long rx_diff  = rx_now - rx;

      cpu = cpu_now;
      tx  = tx_now;
      rx  = rx_now;

      float energy_this_period =
          (cpu_diff * 1.8f + tx_diff * 17.4f + rx_diff * 18.8f) / 1000.0f;
      total_energy_mJ += energy_this_period;

      if(!is_dead && total_energy_mJ >= 10000.0f) {
        is_dead = 1;
        LOG_INFO("DEAD %u %lu\n", node_id, clock_time());
      }
    }
  }
  printf("This is my hello world!");
  PROCESS_END();
}

/*
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 * 3. Neither the name of the Institute nor the names of its contributors
 *    may be used to endorse or promote products derived from this software
 *    without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE INSTITUTE AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE INSTITUTE OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * This file is part of the Contiki operating system.
 *
 */

#include "contiki.h"
#include "net/routing/routing.h"
#include "net/netstack.h"
#include "net/ipv6/simple-udp.h"
#include "sys/log.h"
#include "node-id.h"
#include "positions-simulation.h"

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT	8765
#define UDP_SERVER_PORT	5678

#define SIM_END_MS       5500000UL   // total runtime in ms (e.g. 5000s = ~83 min)with 10% margin for wrapup

typedef struct {
  uint32_t t_sent;
  uint16_t origin_id;
  char     padding[118];
} app_packet_t;

/* Per-node statistics */
typedef struct {
  uint32_t recv_count;
  uint64_t total_latency;
  uint32_t latency_count;
  int32_t  min_latency;
  int32_t  max_latency;
} node_stats_t;

static node_stats_t stats[NUM_NODES+1];   // index 0 dummy, 1 sink, 2..N motes

static void
wrapup(void) {
  LOG_INFO("WRAPUP sink end_ms=%"PRIu32"\n",
           (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND));

  for(uint16_t i = 2; i <= NUM_NODES; i++) {
    if(stats[i].recv_count > 0) {
      uint32_t avg_latency = (stats[i].latency_count > 0) ?
        (uint32_t)(stats[i].total_latency / stats[i].latency_count) : 0;
      LOG_INFO("SINK_SUMMARY node=%u Recv=%"PRIu32
               " AvgE2E=%ums MinE2E=%ums MaxE2E=%ums\n",
               i, stats[i].recv_count,
               avg_latency,
               stats[i].min_latency,
               stats[i].max_latency);
    } else {
      LOG_INFO("SINK_SUMMARY node=%u Recv=0\n", i);
    }
  }
}

static int
is_simulation_time_over(void) {
  uint32_t now_ms = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
  return (now_ms >= SIM_END_MS);
}

/*---------------------------------------------------------------------------*/
static void
udp_rx_callback(struct simple_udp_connection *c,
         const uip_ipaddr_t *sender_addr,
         uint16_t sender_port,
         const uip_ipaddr_t *receiver_addr,
         uint16_t receiver_port,
         const uint8_t *data,
         uint16_t datalen)
{
  if(datalen == sizeof(app_packet_t)) {
      app_packet_t pkt;
      memcpy(&pkt, data, sizeof(pkt));
      uint32_t t_recv = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
      int32_t latency = (int32_t)(t_recv - pkt.t_sent);
      if(latency < 0) latency = 0;
      // Update per-node stats
      if(pkt.origin_id <= NUM_NODES && pkt.origin_id >= 2) {
        node_stats_t *s = &stats[pkt.origin_id];
        s->recv_count++;
        s->total_latency += latency;
        s->latency_count++;
        if(latency < s->min_latency) s->min_latency = latency;
        if(latency > s->max_latency) s->max_latency = latency;
      }
      LOG_INFO("RX origin=%u latency=%"PRId32"ms size=%uB\n",
               pkt.origin_id, latency, datalen);
  } else {
      LOG_WARN("RX wrong size=%u from ", datalen);
      LOG_WARN_6ADDR(sender_addr);
      LOG_WARN_("\n");
  }
}
/*---------------------------------------------------------------------------*/

static struct simple_udp_connection udp_conn;

PROCESS(udp_server_process, "SINK");
AUTOSTART_PROCESSES(&udp_server_process);
PROCESS_THREAD(udp_server_process, ev, data)
{
  static struct etimer t;
  uint32_t ticks_left, step; 
  PROCESS_BEGIN();
  // Init stats
  for(int i = 0; i <= NUM_NODES; i++) {
    stats[i].recv_count = 0;
    stats[i].total_latency = 0;
    stats[i].latency_count = 0;
    stats[i].min_latency = INT32_MAX;
    stats[i].max_latency = 0;
  }
  /* Initialize DAG root */
  NETSTACK_ROUTING.root_start();
  /* Initialize UDP connection */
  simple_udp_register(&udp_conn, UDP_SERVER_PORT, NULL,
                      UDP_CLIENT_PORT, udp_rx_callback);
  step = 60000; // check every ~60s of sim time
  while(1) {
    etimer_set(&t, step);
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&t));
    if(is_simulation_time_over()) {
      wrapup();
      PROCESS_EXIT();
    }
}
  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

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
#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT	8765
#define UDP_SERVER_PORT	5678

#define SIM_END_MS       5500000UL   // total runtime in ms (e.g. 5000s = ~83 min)with 10% margin for wrapup

typedef struct {
  uint32_t t_sent;       // send timestamp (ms, from clock_time)
  uint8_t  padding[124]; // filler to make total size = 128 bytes
} __attribute__((packed)) app_packet_t;

static struct simple_udp_connection udp_conn;

PROCESS(udp_server_process, "SINK");
AUTOSTART_PROCESSES(&udp_server_process);
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

      LOG_INFO("RX from ");
      LOG_INFO_6ADDR(sender_addr);
      LOG_INFO_(" t_sent=%"PRIu32" t_recv=%"PRIu32" latency=%"PRId32"ms size=%uB\n",
               pkt.t_sent, t_recv, latency, datalen);
  } else {
      LOG_WARN("RX wrong size=%u from ", datalen);
      LOG_WARN_6ADDR(sender_addr);
      LOG_WARN_("\n");
  }
}
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(udp_server_process, ev, data)
{
  PROCESS_BEGIN();

  /* Initialize DAG root */
  NETSTACK_ROUTING.root_start();

  /* Initialize UDP connection */
  simple_udp_register(&udp_conn, UDP_SERVER_PORT, NULL,
                      UDP_CLIENT_PORT, udp_rx_callback);

  ticks_left = (SIM_END_MS * CLOCK_SECOND) / 1000;  // convert ms to ticks

  while(ticks_left > 0) {
    step = ticks_left > 60000 ? 60000 : ticks_left; // safe max
    etimer_set(&t, step);
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&t));
    ticks_left -= step;
  }
  
  LOG_INFO("WRAPUP sink: dumping final metrics at end of sim\n");

  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

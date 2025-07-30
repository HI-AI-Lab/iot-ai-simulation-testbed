#include "contiki.h"
#include "net/netstack.h"
#include "net/routing/routing.h"
#include "net/ipv6/uip-ds6.h"
#include "sys/node-id.h"
#include "sys/log.h"
#include <stdio.h>

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

PROCESS(app_process, "OF0 Packet Sender (RPL Lite)");
AUTOSTART_PROCESSES(&app_process);

static struct etimer et;
static uint16_t seq_id = 0;

PROCESS_THREAD(app_process, ev, data)
{
  static uip_ipaddr_t dest_ipaddr;

  PROCESS_BEGIN();
  etimer_set(&et, CLOCK_SECOND * 1);

  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&et));
    etimer_reset(&et);

    if(NETSTACK_ROUTING.node_is_reachable() &&
       NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {
      LOG_INFO("SEND %u %lu %u\n", node_id, clock_time(), seq_id++);
    }
  }

  PROCESS_END();
}

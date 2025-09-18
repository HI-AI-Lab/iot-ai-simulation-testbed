#include "contiki.h"
#include "net/routing/routing.h"
#include "random.h"
#include "net/netstack.h"
#include "net/ipv6/simple-udp.h"
#include "sys/log.h"
#include "sys/node-id.h"
#include <stdio.h>
#include <string.h>

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT 8765
#define UDP_SERVER_PORT 5678

static struct simple_udp_connection udp_conn;
static struct etimer periodic_timer;
static unsigned long seqno = 0;

PROCESS(udp_client_process, "UDP client");
AUTOSTART_PROCESSES(&udp_client_process);

PROCESS_THREAD(udp_client_process, ev, data)
{
  uip_ipaddr_t root_ipaddr;
  PROCESS_BEGIN();
  
  LOG_INFO("App starting on node=%u, interval=%lu ticks (%.2f sec)\n",
         node_id,
         (unsigned long)(SEND_INTERVAL_SEC * CLOCK_SECOND),
         (double)SEND_INTERVAL_SEC);

  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL, UDP_SERVER_PORT, NULL);

  /* Random startup delay */
  etimer_set(&periodic_timer, random_rand() % (3 * CLOCK_SECOND));

  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));

    if(NETSTACK_ROUTING.node_is_reachable() &&
       NETSTACK_ROUTING.get_root_ipaddr(&root_ipaddr)) {

      char buf[32];
      snprintf(buf, sizeof(buf), "node=%u;seq=%lu", node_id, seqno);

      LOG_INFO("TX\tnode=%u\tseq=%lu\n", node_id, seqno);

      simple_udp_sendto(&udp_conn, buf, strlen(buf), &root_ipaddr);
      seqno++;
    }

    etimer_set(&periodic_timer, (clock_time_t)(SEND_INTERVAL_SEC * CLOCK_SECOND));
  }

  PROCESS_END();
}

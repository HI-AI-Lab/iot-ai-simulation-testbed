#include "contiki.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/ipv6/simple-udp.h"
#include "sys/log.h"
#include "sys/energest.h"
#include "net/packetbuf.h"
#include "lib/random.h"

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_PORT 1234
#define SEND_INTERVAL (CLOCK_SECOND * 10)   /* adjust traffic rate */

static struct simple_udp_connection udp_conn;
static struct etimer periodic_timer;
static unsigned seqno = 0;

/* Counters for QLR */
static unsigned sent_pkts = 0;
static unsigned dropped_pkts = 0;

/*---------------------------------------------------------------------------*/
PROCESS(node_process, "RPL Node Process");
AUTOSTART_PROCESSES(&node_process);
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(node_process, ev, data)
{
  static uip_ipaddr_t dest_addr;

  PROCESS_BEGIN();

  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, NULL);

  etimer_set(&periodic_timer, SEND_INTERVAL);

  LOG_INFO("JOINER node=%u started\n", linkaddr_node_addr.u8[7]);

  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));
    etimer_reset(&periodic_timer);

    if(NETSTACK_ROUTING.node_is_reachable() &&
       NETSTACK_ROUTING.get_root_ipaddr(&dest_addr)) {
      char buf[64];
      clock_time_t now = clock_time();
      snprintf(buf, sizeof(buf), "SEQ:%u TS:%lu", seqno, (unsigned long)now);

      sent_pkts++;
      int ret = simple_udp_sendto(&udp_conn, buf, strlen(buf), &dest_addr);

      if(ret) {
        LOG_INFO("DEBUG SEND node=%u seq=%u\n",
                 linkaddr_node_addr.u8[7], seqno);
      } else {
        dropped_pkts++;
      }

      double qlr = (sent_pkts == 0) ? 0.0 :
                   (double)dropped_pkts / (double)sent_pkts;
      LOG_INFO("METRIC QLR node=%u qlr=%.3f sent=%u dropped=%u\n",
               linkaddr_node_addr.u8[7], qlr, sent_pkts, dropped_pkts);

      seqno++;
    }
  }

  PROCESS_END();
}

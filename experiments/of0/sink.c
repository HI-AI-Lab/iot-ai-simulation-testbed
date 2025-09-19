#include "contiki.h"
#include "net/routing/routing.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/ipv6/uip-ds6.h"
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

static void
udp_rx_callback(struct simple_udp_connection *c,
                const uip_ipaddr_t *sender_addr,
                uint16_t sender_port,
                const uip_ipaddr_t *receiver_addr,
                uint16_t receiver_port,
                const uint8_t *data,
                uint16_t datalen)
{
  (void)c; (void)sender_addr; (void)sender_port;
  (void)receiver_addr; (void)receiver_port;

  unsigned int node_id = 0;
  unsigned long seq = 0;

  char buf[64];
  if(datalen >= sizeof(buf)) datalen = sizeof(buf) - 1;
  memcpy(buf, data, datalen);
  buf[datalen] = '\0';

  if(sscanf(buf, "node=%u;seq=%lu", &node_id, &seq) == 2) {
    LOG_INFO("RX\tnode=%u\tseq=%lu\n", node_id, seq);
  }
}

PROCESS(udp_server_process, "UDP server");
AUTOSTART_PROCESSES(&udp_server_process);

PROCESS_THREAD(udp_server_process, ev, data)
{
  PROCESS_BEGIN();

  LOG_INFO("Routing driver active: %s\n", NETSTACK_ROUTING.name);

  /* Configure prefix for the root */
  uip_ipaddr_t ip;
  uip_ip6addr(&ip, 0x2001,0xdb8,0,0,0,0,0,0);  // global /64
  uip_ds6_set_addr_iid(&ip, &uip_lladdr);
  uip_ds6_addr_add(&ip, 0, ADDR_AUTOCONF);

  LOG_INFO("ROOT GLOBAL "); LOG_INFO_6ADDR(&ip); LOG_INFO_("\n");

  /* Wait until a preferred global address is available */
  uip_ds6_addr_t *a = NULL;
  do {
    PROCESS_PAUSE();
    a = uip_ds6_get_global(ADDR_PREFERRED);
  } while(a == NULL);

  LOG_INFO("Preferred global? yes\n");

  /* Start as RPL root */
  NETSTACK_ROUTING.root_start();

  rpl_instance_t *inst = NULL;
  do {
    PROCESS_PAUSE();
    inst = rpl_get_default_instance();
  } while(inst == NULL);

  LOG_INFO("ROOT STARTED (node 1)\n");

/*
  // Explicitly set DAG prefix 
  rpl_instance_t *inst = rpl_get_default_instance();
  if(inst) {
    rpl_prefix_t *p = &inst->dag.prefix_info;
    uip_ip6addr_copy(&p->prefix, &a->ipaddr);  // copy IPv6 address
    p->length = 64;                            // prefix length in bits

    rpl_set_prefix(p);                         // install it into the DAG

    LOG_INFO("DODAG prefix set to "); LOG_INFO_6ADDR(&a->ipaddr); LOG_INFO_("\n");
    LOG_INFO("DODAG confirmed: instance_id=%u, rank=%u\n",
             inst->instance_id, inst->dag.rank);
  } else {
    LOG_WARN("No active RPL instance after root_start()\n");
  }
*/

  /* Register UDP server */
  simple_udp_register(&udp_conn, UDP_SERVER_PORT, NULL,
                      UDP_CLIENT_PORT, udp_rx_callback);

  /* Print root global IPv6 address */
  if(a != NULL) {
    LOG_INFO("Root global IPv6 address: ");
    LOG_INFO_6ADDR(&a->ipaddr);
    LOG_INFO_("\n");
  }

  PROCESS_END();
}
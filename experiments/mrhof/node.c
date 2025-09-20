#include "contiki.h"
#include "random.h"
#include "net/routing/routing.h"
#include "net/netstack.h"
#include "net/ipv6/simple-udp.h"
#include "sys/log.h"
#include <stdint.h>
#include <inttypes.h>
#include <math.h>

#define LOG_MODULE "App"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_CLIENT_PORT	8765
#define UDP_SERVER_PORT	5678

/* Default if not provided from Makefile */
/*
#ifndef SEND_INTERVAL_MS
#define SEND_INTERVAL_MS 750  // ~80 PPM
#endif
*/
static struct simple_udp_connection udp_conn;
static uint32_t rx_count = 0;

typedef struct {
  uint32_t t_sent;       // send timestamp (ms, from clock_time)
  uint8_t  padding[124]; // filler to make total size = 128 bytes
} __attribute__((packed)) app_packet_t;

/* Return an exponential( mean = SEND_INTERVAL_MS ) delay in Contiki ticks */
static clock_time_t
poisson_next_delay_ticks(void)
{
  /* U in (0,1]; avoid 0 to protect logf */
  float u = ((float)random_rand() + 1.0f) / ((float)RANDOM_RAND_MAX + 1.0f);

  /* mean seconds = SEND_INTERVAL_MS / 1000.0 */
  float mean_sec = (float)SEND_INTERVAL_MS / 1000.0f;

  /* exponential sample: X = -mean * ln(U)  (seconds) */
  float x_sec = -mean_sec * logf(u);

  /* convert to ticks */
  clock_time_t ticks = (clock_time_t)(x_sec * (float)CLOCK_SECOND);

  /* never return 0 ticks */
  if(ticks < 1) ticks = 1;

  return ticks;
}

/*---------------------------------------------------------------------------*/
PROCESS(udp_client_process, "NODE");
AUTOSTART_PROCESSES(&udp_client_process);
/*---------------------------------------------------------------------------*/
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(udp_client_process, ev, data)
{
  static struct etimer periodic_timer;
  static char str[32];
  uip_ipaddr_t dest_ipaddr;
  static uint32_t tx_count;
  static uint32_t missed_tx_count;

  PROCESS_BEGIN();
  
  unsigned long ppm = (SEND_INTERVAL_MS > 0) ? 60000UL / (unsigned long)SEND_INTERVAL_MS : 0;
  LOG_INFO("mean_interval_ms=%lu (~PPM=%lu) [Poisson]\n",(unsigned long)SEND_INTERVAL_MS, ppm);

  /* Initialize UDP connection */
  simple_udp_register(&udp_conn, UDP_CLIENT_PORT, NULL,
                      UDP_SERVER_PORT, NULL);

  /* before loop: schedule first send with Poisson gap */
  etimer_set(&periodic_timer, poisson_next_delay_ticks());
  
  while(1) {
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&periodic_timer));

    if(NETSTACK_ROUTING.node_is_reachable() &&
        NETSTACK_ROUTING.get_root_ipaddr(&dest_ipaddr)) {

      /* Print statistics every 100th TX */
      if(tx_count % 100 == 0) {
        LOG_INFO("Tx/Rx/MissedTx: %" PRIu32 "/%" PRIu32 "/%" PRIu32 "\n",
                 tx_count, rx_count, missed_tx_count);
      }

      /* Send to DAG root */
      app_packet_t pkt;
      pkt.t_sent = (uint32_t)(clock_time() * 1000UL / CLOCK_SECOND);
      memset(pkt.padding, 0, sizeof(pkt.padding));
      simple_udp_sendto(&udp_conn, &pkt, sizeof(pkt), &dest_ipaddr);
      tx_count++;
    } else {
      LOG_INFO("Not reachable yet\n");
      if(tx_count > 0) {
        missed_tx_count++;
      }
    }

    /* inside the loop after you handle a send attempt */
    etimer_set(&periodic_timer, poisson_next_delay_ticks());
  }

  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

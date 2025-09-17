#include "contiki.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/netstack.h"
#include "net/ipv6/simple-udp.h"
#include "sys/log.h"
#include "sys/energest.h"
#include "random.h"

#define LOG_MODULE "APP"
#define LOG_LEVEL LOG_LEVEL_INFO

#define UDP_PORT 1234

static struct simple_udp_connection udp_conn;

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
  LOG_INFO("DEBUG RECV from ");
  LOG_INFO_6ADDR(sender_addr);
  LOG_INFO_(" len=%u\n", datalen);

  /* --- PRR counter --- */
  static uint32_t recv_count = 0;
  recv_count++;
  LOG_INFO("METRIC PRR_GLOBAL recv=%lu\n", (unsigned long)recv_count);

  /* --- Extract timestamp from payload and compute E2E latency --- */
  unsigned seq = 0;
  unsigned long sent_ts = 0;
  if(sscanf((const char *)data, "SEQ:%u TS:%lu", &seq, &sent_ts) == 2) {
    clock_time_t now = clock_time();
    long latency_ticks = (long)now - (long)sent_ts;
    long latency_ms = (latency_ticks * 1000) / CLOCK_SECOND;

    LOG_INFO("METRIC E2E seq=%u latency=%ldms\n", seq, latency_ms);
  } else {
    LOG_WARN("Could not parse payload for latency\n");
  }
}
/*---------------------------------------------------------------------------*/
PROCESS(sink_process, "Sink (RPL Root)");
AUTOSTART_PROCESSES(&sink_process);
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(sink_process, ev, data)
{
  PROCESS_BEGIN();

  /* Start this mote as the RPL root */
  rpl_dag_root_start();

  /* Register UDP server */
  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, udp_rx_callback);

  /* Announce root info */
  LOG_INFO("ROOT DODAG created\n");
  LOG_INFO("ROOT GLOBAL prefix announced\n");

  while(1) {
    PROCESS_YIELD();

    /* Lifetime + energy log */
    static int counter = 0;
    if(++counter % 100 == 0) {
      unsigned long cpu, lpm, deep_lpm, tx, rx;
      energest_flush();
      cpu = energest_type_time(ENERGEST_TYPE_CPU);
      lpm = energest_type_time(ENERGEST_TYPE_LPM);
      deep_lpm = energest_type_time(ENERGEST_TYPE_DEEP_LPM);
      tx = energest_type_time(ENERGEST_TYPE_TRANSMIT);
      rx = energest_type_time(ENERGEST_TYPE_LISTEN);

      LOG_INFO("METRIC NLT DEAD node=1 cpu=%lu lpm=%lu deep=%lu tx=%lu rx=%lu\n",
               cpu, lpm, deep_lpm, tx, rx);
    }
  }

  PROCESS_END();
}
/*---------------------------------------------------------------------------*/

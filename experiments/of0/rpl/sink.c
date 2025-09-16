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

static struct simple_udp_connection udp_conn;

/* Counters for metrics */
static unsigned total_recv = 0;
static unsigned expected_seq = 0;

/*---------------------------------------------------------------------------*/
PROCESS(sink_process, "RPL Root Process");
AUTOSTART_PROCESSES(&sink_process);
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
  total_recv++;

  LOG_INFO("DEBUG RECV node=");
  LOG_INFO_6ADDR(sender_addr);
  LOG_INFO_(" len=%u data='%.*s'\n", datalen, datalen, (char *)data);

  /* Extract sequence number for E2E latency */
  unsigned seq;
  clock_time_t tx_time;
  if(sscanf((const char *)data, "SEQ:%u TS:%lu", &seq, &tx_time) == 2) {
    clock_time_t now = clock_time();
    unsigned latency = (unsigned)((now - tx_time) * 1000 / CLOCK_SECOND);

    LOG_INFO("METRIC E2E seq=%u latency_ms=%u\n", seq, latency);

    /* PRR estimation */
    if(seq > expected_seq) {
      expected_seq = seq;
    }
    double prr = (double) total_recv / (double) expected_seq;
    LOG_INFO("METRIC PRR_GLOBAL prr=%.3f recv=%u expected=%u\n",
             prr, total_recv, expected_seq);
  }
}
/*---------------------------------------------------------------------------*/
PROCESS_THREAD(sink_process, ev, data)
{
  PROCESS_BEGIN();

  NETSTACK_ROUTING.root_start();

  simple_udp_register(&udp_conn, UDP_PORT, NULL, UDP_PORT, udp_rx_callback);

  LOG_INFO("ROOT DODAG created\n");
  LOG_INFO("ROOT GLOBAL prefix announced\n");

  while(1) {
    PROCESS_YIELD();
  }

  PROCESS_END();
}

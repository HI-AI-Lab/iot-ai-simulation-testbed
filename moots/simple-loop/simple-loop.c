#include "contiki.h"
#include <stdio.h>
#include "sys/node-id.h"

PROCESS(hello_loop_process, "Hello Loop Demo");
AUTOSTART_PROCESSES(&hello_loop_process);

PROCESS_THREAD(hello_loop_process, ev, data)
{
  static struct etimer et;
  PROCESS_BEGIN();

  while(1) {
    printf("MOTE RUNNING: node_id=%u\n", node_id);
    fflush(stdout);
    etimer_set(&et, CLOCK_SECOND * 2);
    PROCESS_WAIT_EVENT_UNTIL(etimer_expired(&et));
  }

  PROCESS_END();
}

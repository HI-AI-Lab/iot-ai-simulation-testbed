#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* ---------- Stack & Routing ---------- */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver   /* RPL Lite */

#define RPL_CONF_OF          rpl_of0            /* Force OF0 (struct) */
#define RPL_CONF_OF_OCP      RPL_OCP_OF0        /* Force OF0 (OCP)    */

#undef UIP_CONF_ROUTER
#define UIP_CONF_ROUTER 1

/* ---------- App traffic rate (choose ONE set) ----------
 * 80 ppm  -> 0.75 s
 * 100 ppm -> 0.60 s
 * 120 ppm -> 0.50 s
 */
#define SEND_INTERVAL_SEC 0.75   /* ~80 packets/min */
// #define SEND_INTERVAL_SEC 0.60   /* ~100 packets/min */
// #define SEND_INTERVAL_SEC 0.50   /* ~120 packets/min */

/* ---------- Buffers & Energy ---------- */
#define QUEUEBUF_CONF_NUM 16
#define ENERGEST_CONF_ON  1

/* ---------- Logging Verbosity ----------
 * Turn RPL to DBG to see DIO/DAG join events & parent switches.
 */
#define LOG_CONF_LEVEL_RPL       LOG_LEVEL_DBG
#define LOG_CONF_LEVEL_IPV6      LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_TCPIP     LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_6LOWPAN   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC       LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_FRAMER    LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* -------------------------------------------------------------------------- */
/* RPL + Routing Configuration                                                */
/* -------------------------------------------------------------------------- */

/* Use RPL-Lite as routing driver */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Force Objective Function 0 (OF0) everywhere */
#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

#undef RPL_CONF_OF_OCP
#define RPL_CONF_OF_OCP RPL_OCP_OF0

/* Enable router functionality */
#undef UIP_CONF_ROUTER
#define UIP_CONF_ROUTER 1

/* -------------------------------------------------------------------------- */
/* Application Traffic                                                        */
/* -------------------------------------------------------------------------- */
/* Choose one packet-per-minute (ppm) rate:
 * 80 ppm  -> 0.75 s interval
 * 100 ppm -> 0.60 s interval
 * 120 ppm -> 0.50 s interval
 */
#define SEND_INTERVAL_SEC 0.75   /* ~80 packets/min */
// #define SEND_INTERVAL_SEC 0.60   /* ~100 packets/min */
// #define SEND_INTERVAL_SEC 0.50   /* ~120 packets/min */

/* -------------------------------------------------------------------------- */
/* Buffers, Energy, and System                                                */
/* -------------------------------------------------------------------------- */
#define QUEUEBUF_CONF_NUM 16
#define ENERGEST_CONF_ON  1

/* -------------------------------------------------------------------------- */
/* Logging Verbosity                                                          */
/* -------------------------------------------------------------------------- */
#define LOG_CONF_LEVEL_RPL       LOG_LEVEL_DBG   /* DIO/DAG events */
#define LOG_CONF_LEVEL_IPV6      LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_TCPIP     LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_6LOWPAN   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC       LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_FRAMER    LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

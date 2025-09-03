#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Use RPL Lite (not RPL Classic) */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Select Objective Function: OF0 */
#undef RPL_CONF_OF_OCP
#define RPL_CONF_OF_OCP RPL_OCP_OF0

/* Router mode */
#undef UIP_CONF_ROUTER
#define UIP_CONF_ROUTER 1

/* Optional tuning */
#define QUEUEBUF_CONF_NUM 16
#define ENERGEST_CONF_ON  1

/* ----------------------------------------------------
 * Traffic generation rate (pick ONE by uncommenting)
 * ----------------------------------------------------
 * 80 ppm  -> 0.75 sec
 * 100 ppm -> 0.60 sec
 * 120 ppm -> 0.50 sec
 */
#define SEND_INTERVAL_SEC 0.75   /* ~80 packets/min */
// #define SEND_INTERVAL_SEC 0.60   /* ~100 packets/min */
// #define SEND_INTERVAL_SEC 0.50   /* ~120 packets/min */

/* Logging */
#define LOG_CONF_LEVEL_RPL     LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_TCPIP   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC     LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_FRAMER  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_6LOWPAN LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

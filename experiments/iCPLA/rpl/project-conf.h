#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Use RPL Lite (Contiki-NG default stack) */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Objective Function: MRHOF (ETX-based) */
#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_mrhof

/* Don’t send IPv6 Router Advertisements from motes */
#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

/* Logging setup */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

/* Optional: fine-tune MRHOF ETX behavior (defaults are usually OK) */
/* #define RPL_CONF_DEFAULT_LINK_METRIC   RPL_DAG_MC_ETX */
/* #define RPL_MRHOF_CONF_MAX_LINK_METRIC 1024 */
/* #define RPL_MRHOF_CONF_ETX_DIVISOR     128 */

#endif /* PROJECT_CONF_H_ */

#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Use RPL Lite (default in Contiki-NG) */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Select our iCPLA Objective Function */
#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_icpla

/* Tune α for iCPLA here (fixed-point ETX scale) */
#ifndef RPL_CONF_ETX_DIVISOR
/* Usually provided by Contiki-NG as RPL_ETX_DIVISOR; define alias if missing */
#define RPL_CONF_ETX_DIVISOR 128
#endif

/* Example: α = 0.5 -> ICPLA_ALPHA_FP = 0.5 * RPL_ETX_DIVISOR */
#ifndef ICPLA_ALPHA_FP
#define ICPLA_ALPHA_FP (RPL_CONF_ETX_DIVISOR / 2)
#endif

/* Disable IPv6 Router Advertisements */
#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

/* Logging levels */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

/* Keep MRHOF/OF0 defaults otherwise */

#endif /* PROJECT_CONF_H_ */

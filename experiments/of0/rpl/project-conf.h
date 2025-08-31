#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* RPL Lite (default) */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Objective Function: OF0 */
#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

/* Don’t send IPv6 Router Advertisements from motes */
#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

/* Logging */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

#endif /* PROJECT_CONF_H_ */

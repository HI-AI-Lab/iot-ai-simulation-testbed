#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* ----- Logging / IPv6 ----- */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

/* ----- Routing: RPL Classic + OF0 (hop-count) ----- */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_classic_driver

#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

/* (optional but tidy) pin OCP to OF0 */
#undef RPL_CONF_OF_OCP
#define RPL_CONF_OF_OCP RPL_OCP_OF0

#endif /* PROJECT_CONF_H_ */

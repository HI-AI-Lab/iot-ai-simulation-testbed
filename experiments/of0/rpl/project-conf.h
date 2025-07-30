#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Compact address logging override */
#ifdef LOG_WITH_COMPACT_ADDR
#undef LOG_WITH_COMPACT_ADDR
#endif
#define LOG_WITH_COMPACT_ADDR 0

/* Set RPL Classic with OF0 */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_classic_driver

#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

/* Logging levels */
#define LOG_CONF_LEVEL_RPL   LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

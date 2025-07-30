#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Logging */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0

/* Use RPL Classic instead of RPL Lite */
#undef UIP_CONF_ROUTER
#define UIP_CONF_ROUTER 1

#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_classic_driver

#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

#undef RPL_CONF_STATS
#define RPL_CONF_STATS 1

#undef RPL_CONF_WITH_DAO_ACK
#define RPL_CONF_WITH_DAO_ACK 1

/* Disable RPL Lite */
#undef RPL_CONF_RPL_LITE
#define RPL_CONF_RPL_LITE 0

/* Logging levels */
#define LOG_CONF_LEVEL_RPL   LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

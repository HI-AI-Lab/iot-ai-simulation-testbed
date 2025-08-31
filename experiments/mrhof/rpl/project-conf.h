#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Select RPL MRHOF (ETX-based) */
#define RPL_CONF_OF_OCP RPL_OCP_MRHOF

/* Enable RPL Lite */
#define UIP_CONF_ROUTER              1
#define NETSTACK_CONF_ROUTING        rpl_classic

/* Configure Queue Buffer / Energest for metrics */
#define QUEUEBUF_CONF_NUM            16
#define ENERGEST_CONF_ON             1

/* Logging */
#define LOG_CONF_LEVEL_RPL           LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6          LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_TCPIP         LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC           LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_FRAMER        LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_6LOWPAN       LOG_LEVEL_WARN

#endif /* PROJECT_CONF_H_ */

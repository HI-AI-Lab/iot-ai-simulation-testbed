#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* RPL Lite */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Use our local iCPLA */
#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_icpla

/* ND6 RA off */
#undef UIP_CONF_ND6_SEND_RA
#define UIP_CONF_ND6_SEND_RA 0

/* Logging */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

/* iCPLA knobs (overridable per build) */
#ifndef RPL_ICPLA_ALPHA
#define RPL_ICPLA_ALPHA (64) /* ≈0.5 */
#endif
#ifndef RPL_ICPLA_PARENT_SWITCH_THRESHOLD
#define RPL_ICPLA_PARENT_SWITCH_THRESHOLD (RPL_ETX_DIVISOR/2) /* 0.5 */
#endif

#endif /* PROJECT_CONF_H_ */

#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Use RPL Classic (RPL-Lite in Contiki-NG) */
#undef  NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING        rpl_classic

/* Select iCPLA Objective Function */
#undef  RPL_CONF_OF
#define RPL_CONF_OF                  rpl_icpla_of
#undef  RPL_CONF_OF_OCP
#define RPL_CONF_OF_OCP              RPL_OCP_ICPLA

/* MAC/LLN knobs consistent with paper Table 1 */
#define ENERGEST_CONF_ON             1
#define QUEUEBUF_CONF_NUM            16

/* Logging */
#define LOG_CONF_LEVEL_RPL           LOG_LEVEL_INFO
#define LOG_CONF_LEVEL_IPV6          LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP         LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_MAC           LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP           LOG_LEVEL_INFO

/* (Optional) tune ε/α/γ at build time */
#define ICPLA_ALPHA                  0.3f
#define ICPLA_GAMMA                  0.7f
#define ICPLA_EPSILON                0.2f

#endif /* PROJECT_CONF_H_ */

#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* Use RPL Lite (Contiki-NG default stack) */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

/* Select our custom iCPLA OF (ETX + α·QLR; α tuned by on-node RL) */
#undef  RPL_CONF_OF
#define RPL_CONF_OF rpl_icpla

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

/* ------- App knobs (overridable via CFLAGS) ------- */
#ifndef APP_CONF_SEND_INTERVAL_SEC
#define APP_CONF_SEND_INTERVAL_SEC 5
#endif

#ifndef APP_CONF_PAYLOAD_SIZE
#define APP_CONF_PAYLOAD_SIZE 128
#endif

/* Synthetic local-queue drop model to exercise QLR under load (0..10) */
#ifndef APP_CONF_DROP_PROB_PER_TEN
#define APP_CONF_DROP_PROB_PER_TEN 2
#endif

/* Energy model (platform-agnostic placeholders) */
#ifndef APP_CONF_V_SUPPLY_V
#define APP_CONF_V_SUPPLY_V 3.0f
#endif
#ifndef APP_CONF_I_CPU_mA
#define APP_CONF_I_CPU_mA   1.8f
#endif
#ifndef APP_CONF_I_TX_mA
#define APP_CONF_I_TX_mA    17.4f
#endif
#ifndef APP_CONF_I_RX_mA
#define APP_CONF_I_RX_mA    18.8f
#endif

/* Energy budget (mJ) – when exceeded, mote logs DEAD and stops sending */
#ifndef APP_CONF_ENERGY_BUDGET_mJ
#define APP_CONF_ENERGY_BUDGET_mJ (120000.0f) /* 120 J */
#endif

#endif /* PROJECT_CONF_H_ */

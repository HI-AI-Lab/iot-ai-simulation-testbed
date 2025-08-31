#ifndef PROJECT_CONF_H_
#define PROJECT_CONF_H_

/* ---- RPL-Lite + OF0 (hop-count) ---- */
#undef NETSTACK_CONF_ROUTING
#define NETSTACK_CONF_ROUTING rpl_lite_driver

#undef RPL_CONF_OF
#define RPL_CONF_OF rpl_of0

/* Logging */
#undef LOG_WITH_COMPACT_ADDR
#define LOG_WITH_COMPACT_ADDR 0
#define LOG_CONF_LEVEL_RPL    LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_IPV6   LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_TCPIP  LOG_LEVEL_WARN
#define LOG_CONF_LEVEL_APP    LOG_LEVEL_INFO

/* App timings / ports */
#define APP_SEND_INTERVAL_SEC   5
#define APP_STATS_INTERVAL_SEC 10
#define APP_UDP_DATA_PORT    8765
#define APP_UDP_ACK_PORT     8766

/* Sender-side queue heuristic */
#define APP_MAX_OUTSTANDING_ACKS 5

/* Energest */
#define ENERGEST_CONF_ON 1

/* Energy model (example numbers, consistent across baselines) */
#define APP_VOLTAGE_V           (3.0f)
#define APP_I_CPU_MA            (1.8f)
#define APP_I_LPM_MA            (0.054f)
#define APP_I_TX_MA             (17.4f)
#define APP_I_RX_MA             (18.8f)
#define APP_ENERGY_BUDGET_MJ (18000.0f)

#endif /* PROJECT_CONF_H_ */

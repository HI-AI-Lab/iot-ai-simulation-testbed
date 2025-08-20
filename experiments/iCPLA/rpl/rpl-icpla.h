#ifndef RPL_ICPLA_H_
#define RPL_ICPLA_H_

#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-of.h"

/* Match Contiki-NG ETX fixed-point scale (128 == 1.0) */
#ifndef LINK_STATS_ETX_DIVISOR
#define LINK_STATS_ETX_DIVISOR 128
#endif
#ifndef ICPLA_FP_DIVISOR
#define ICPLA_FP_DIVISOR LINK_STATS_ETX_DIVISOR
#endif

/* Default alpha (fallback before RL sets it); 16/128 ≈ 0.125 */
#ifndef ICPLA_ALPHA_FP_DEFAULT
#define ICPLA_ALPHA_FP_DEFAULT 16
#endif

/* Exported by the app: smoothed sender-side QLR in fixed-point (/128) */
uint16_t icpla_current_qlr_fp(void);

/* Alpha weight (fixed-point /128) — updated by the app's RL loop */
extern volatile uint16_t icpla_alpha_fp;

/* iCPLA OF instance (defined in rpl-icpla.c) */
extern rpl_of_t rpl_icpla;

#endif /* RPL_ICPLA_H_ */

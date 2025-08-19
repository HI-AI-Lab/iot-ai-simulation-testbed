#ifndef RPL_ICPLA_H
#define RPL_ICPLA_H

#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-of.h"

/* α in ETX fixed-point units (RPL_ETX_DIVISOR = 128). 64 ≈ 0.5 */
#ifndef RPL_ICPLA_ALPHA
#define RPL_ICPLA_ALPHA (64)
#endif

/* Hysteresis to avoid flapping, also in ETX fixed-point units */
#ifndef RPL_ICPLA_PARENT_SWITCH_THRESHOLD
#define RPL_ICPLA_PARENT_SWITCH_THRESHOLD (RPL_ETX_DIVISOR/2) /* 0.5 */
#endif

/* QLR provider: returns fixed-point in [0..RPL_ETX_DIVISOR].
 * We'll implement icpla_current_qlr_fp() in app.c shortly.
 */
extern uint16_t icpla_current_qlr_fp(void);

extern rpl_of_t rpl_icpla;

#endif /* RPL_ICPLA_H */

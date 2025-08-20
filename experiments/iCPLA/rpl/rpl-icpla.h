#ifndef RPL_ICPLA_H
#define RPL_ICPLA_H

#include "contiki.h"
#include "net/routing/rpl-lite/rpl.h"

/* iCPLA scoring:
 *   link_cost = ETX  +  α * QLR_sender
 * We use Contiki-NG's fixed-point convention for ETX: RPL_ETX_DIVISOR.
 * QLR_sender is returned as a fixed-point value in the same scale.
 *
 * α is configured via PROJECT_CONF (defaults below if not defined).
 */
#ifndef ICPLA_ALPHA_FP
/* Default α = 0.5 in ETX fixed-point units (RPL_ETX_DIVISOR is typically 128) */
#define ICPLA_ALPHA_FP (RPL_ETX_DIVISOR / 2)
#endif

/* The OF instance */
extern rpl_of_t rpl_icpla;

/* Application can override this (weak) to feed sender-side QLR
 * scaled in ETX fixed-point units (0 .. RPL_ETX_DIVISOR).
 * If not provided by the app, this returns 0 (behaves like MRHOF).
 */
uint16_t icpla_get_local_qlr_fp(void);

#endif /* RPL_ICPLA_H */

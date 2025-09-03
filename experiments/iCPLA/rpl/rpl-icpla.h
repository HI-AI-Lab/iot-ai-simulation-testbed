#ifndef RPL_ICPLA_H
#define RPL_ICPLA_H

#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-of.h"
#include "net/ipv6/uip.h"
#include "sys/ctimer.h"

/* Custom OCP for iCPLA (not conflicting with OF0/MRHOF). */
#ifndef RPL_OCP_ICPLA
#define RPL_OCP_ICPLA 0xFFFD
#endif

/* Q-learning knobs (paper §3.4, §4.2) */
#ifndef ICPLA_ALPHA
#define ICPLA_ALPHA  0.3f   /* learning rate α */
#endif
#ifndef ICPLA_GAMMA
#define ICPLA_GAMMA  0.7f   /* discount β */
#endif
#ifndef ICPLA_EPSILON
#define ICPLA_EPSILON 0.2f  /* ε-greedy exploration */
#endif

/* Keep a small Q-table per candidate parent */
#define ICPLA_MAX_PARENTS 16

/* Sliding window for mean collision prob (Eq.8) */
#define ICPLA_COLL_WINDOW 5

/* Public OF symbol */
extern rpl_of_t rpl_icpla_of;

/* Expose helpers so app (or platform) can poke if needed */
void icpla_notify_tx_collision(void);
void icpla_notify_tx_success(void);

#endif /* RPL_ICPLA_H */

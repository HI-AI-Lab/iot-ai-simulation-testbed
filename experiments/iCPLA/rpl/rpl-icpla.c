#include "contiki.h"
#include "sys/log.h"

#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/routing/rpl-lite/rpl-private.h"

#include "rpl-icpla.h"

#define LOG_MODULE "RPL-ICPLA"
#define LOG_LEVEL LOG_LEVEL_INFO

/* If the app does not provide a QLR callback, default to zero (MRHOF-like) */
__attribute__((weak))
uint16_t icpla_get_local_qlr_fp(void) {
  return 0;
}

/* Parent link ETX in fixed-point (/RPL_ETX_DIVISOR) */
static uint16_t
link_etx_fp(const rpl_parent_t *p)
{
  /* RPL-Lite provides the per-parent link metric in ETX fixed-point */
  return rpl_parent_get_link_metric((rpl_parent_t *)p);
}

/* iCPLA link cost: ETX + α·QLR  (all in ETX fixed-point) */
static uint16_t
icpla_link_cost_fp(const rpl_parent_t *p)
{
  const uint16_t etx_fp = link_etx_fp(p);
  const uint16_t qlr_fp = icpla_get_local_qlr_fp();

  /* (alpha * qlr_fp) / RPL_ETX_DIVISOR, with rounding */
  const uint32_t mix = (uint32_t)ICPLA_ALPHA_FP * (uint32_t)qlr_fp;
  const uint16_t alpha_term = (uint16_t)((mix + (RPL_ETX_DIVISOR / 2)) / RPL_ETX_DIVISOR);

  uint32_t cost = (uint32_t)etx_fp + alpha_term;
  if(cost > 0xFFFF) cost = 0xFFFF;
  return (uint16_t)cost;
}

/* Convert ETX-like fixed-point to rank increase units */
static uint16_t
icpla_cost_to_rank_inc(uint16_t cost_fp)
{
  /* Same mapping MRHOF uses: scale by RPL_MIN_HOPRANKINC / RPL_ETX_DIVISOR */
  return (uint16_t)(((uint32_t)cost_fp * RPL_MIN_HOPRANKINC + (RPL_ETX_DIVISOR/2)) / RPL_ETX_DIVISOR);
}

/* ----- rpl_of_t hooks ------------------------------------------------ */

static void
reset(rpl_dag_t *dag)
{
  (void)dag;
  LOG_INFO("reset()\n");
}

static void
parent_state_callback(rpl_parent_t *p, int known, int etx)
{
  (void)p; (void)known; (void)etx;
  /* No special bookkeeping: we read metrics on demand */
}

static rpl_parent_t *
best_parent(rpl_parent_t *p1, rpl_parent_t *p2)
{
  if(!p1) return p2;
  if(!p2) return p1;

  const uint16_t c1 = icpla_link_cost_fp(p1);
  const uint16_t c2 = icpla_link_cost_fp(p2);

  const uint32_t path1 = (uint32_t)rpl_parent_get_rank(p1) + icpla_cost_to_rank_inc(c1);
  const uint32_t path2 = (uint32_t)rpl_parent_get_rank(p2) + icpla_cost_to_rank_inc(c2);

  if(path1 < path2) return p1;
  if(path2 < path1) return p2;

  /* tie-breakers: smaller link cost, then smaller parent rank */
  if(c1 < c2) return p1;
  if(c2 < c1) return p2;

  const uint16_t r1 = rpl_parent_get_rank(p1);
  const uint16_t r2 = rpl_parent_get_rank(p2);
  if(r1 < r2) return p1;
  if(r2 < r1) return p2;

  /* final tie: keep current (p1) */
  return p1;
}

static rpl_rank_t
calculate_rank(const rpl_parent_t *p, rpl_rank_t base_rank)
{
  if(!p) {
    /* Root or no parent */
    return base_rank == 0 ? RPL_MIN_HOPRANKINC : base_rank;
  }

  if(base_rank == 0) {
    base_rank = rpl_parent_get_rank(p);
    if(base_rank == 0) {
      return INFINITE_RANK;
    }
  }

  const uint16_t cost_fp = icpla_link_cost_fp(p);
  const uint16_t inc = icpla_cost_to_rank_inc(cost_fp);

  uint32_t new_rank = (uint32_t)base_rank + inc;
  if(new_rank > INFINITE_RANK) new_rank = INFINITE_RANK;
  return (rpl_rank_t)new_rank;
}

static void
update_metric_container(rpl_instance_t *instance)
{
  /* Advertise ETX MC like MRHOF so interop stays sane */
  rpl_metric_object_t *mc = &instance->mc;
  mc->type   = RPL_DAG_MC_ETX;
  mc->flags  = 0;
  mc->aggr   = RPL_DAG_MC_AGGR_ADDITIVE;
  mc->prec   = 0;
  mc->length = sizeof(uint16_t);
  mc->obj.etx = 0; /* optional: could export DAG path ETX if you track it */
}

rpl_of_t rpl_icpla = {
  .reset                  = reset,
  .parent_state_callback  = parent_state_callback,
  .best_parent            = best_parent,
  .calculate_rank         = calculate_rank,
  .update_metric_container= update_metric_container,
  .objective_code_point   = 0xF1, /* experimental/private OCP */
  .of_id                  = "iCPLA(ETX+alpha*QLR)"
};

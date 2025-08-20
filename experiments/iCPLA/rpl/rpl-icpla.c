#include "contiki.h"
#include "sys/log.h"
#include "rpl-icpla.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/routing/rpl-lite/rpl-private.h"

#define LOG_MODULE "RPL-ICPLA"
#define LOG_LEVEL LOG_LEVEL_INFO

/* RL-updatable alpha (/128). App may change at runtime. */
volatile uint16_t icpla_alpha_fp = ICPLA_ALPHA_FP_DEFAULT;

/* ---- Helpers --------------------------------------------------- */

/* Link cost if we use parent p: ETX + alpha * QLR, all /128 fixed-point */
static uint16_t
icpla_link_cost_fp(const rpl_parent_t *p)
{
  /* Parent link ETX already in /128 fixed-point */
  uint16_t etx_fp = rpl_parent_get_link_metric(p);

  /* Sender-side QLR (provided by app), also /128 */
  uint16_t qlr_fp = icpla_current_qlr_fp();

  /* (alpha * QLR) with rounding */
  uint32_t term_alpha = ((uint32_t)icpla_alpha_fp * (uint32_t)qlr_fp + (ICPLA_FP_DIVISOR/2))
                        / ICPLA_FP_DIVISOR;

  uint32_t cost = (uint32_t)etx_fp + term_alpha;
  if(cost > 0xFFFF) cost = 0xFFFF;
  return (uint16_t)cost;
}

/* Map fixed-point cost (/128) to RPL rank increase units */
static uint16_t
icpla_cost_to_rank_increase(uint16_t cost_fp)
{
  return (uint16_t)(((uint32_t)cost_fp * RPL_MIN_HOPRANKINC + (ICPLA_FP_DIVISOR/2))
                    / ICPLA_FP_DIVISOR);
}

/* ---- OF interface ---------------------------------------------- */

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
  /* No-op; ICPLA reads live metrics on demand */
}

static rpl_parent_t *
best_parent(rpl_parent_t *p1, rpl_parent_t *p2)
{
  if(!p1) return p2;
  if(!p2) return p1;

  uint16_t c1 = icpla_link_cost_fp(p1);
  uint16_t c2 = icpla_link_cost_fp(p2);

  uint32_t path1 = (uint32_t)rpl_parent_get_rank(p1) + icpla_cost_to_rank_increase(c1);
  uint32_t path2 = (uint32_t)rpl_parent_get_rank(p2) + icpla_cost_to_rank_increase(c2);

  if(path1 < path2) return p1;
  if(path2 < path1) return p2;

  /* Tie-breakers */
  if(c1 < c2) return p1;
  if(c2 < c1) return p2;

  uint16_t r1 = rpl_parent_get_rank(p1), r2 = rpl_parent_get_rank(p2);
  if(r1 < r2) return p1;
  if(r2 < r1) return p2;

  return rpl_parent_cmp_addr(p1, p2) <= 0 ? p1 : p2;
}

static rpl_rank_t
calculate_rank(const rpl_parent_t *p, rpl_rank_t base_rank)
{
  if(!p) return base_rank == 0 ? RPL_MIN_HOPRANKINC : base_rank;

  if(base_rank == 0) {
    base_rank = rpl_parent_get_rank(p);
    if(base_rank == 0) return INFINITE_RANK;
  }

  uint16_t cost_fp = icpla_link_cost_fp(p);
  uint16_t inc = icpla_cost_to_rank_increase(cost_fp);

  uint32_t new_rank = (uint32_t)base_rank + inc;
  if(new_rank > INFINITE_RANK) new_rank = INFINITE_RANK;
  return (rpl_rank_t)new_rank;
}

static void
update_metric_container(rpl_instance_t *instance)
{
  /* Advertise ETX MC for interop; OF stays internal */
  rpl_metric_object_t *mc = &instance->mc;
  mc->type = RPL_DAG_MC_ETX;
  mc->flags = 0;
  mc->aggr = RPL_DAG_MC_AGGR_ADDITIVE;
  mc->prec = 0;
  mc->length = sizeof(uint16_t);
  mc->obj.etx = 0;
}

rpl_of_t rpl_icpla = {
  .reset                   = reset,
  .parent_state_callback   = parent_state_callback,
  .best_parent             = best_parent,
  .calculate_rank          = calculate_rank,
  .update_metric_container = update_metric_container,
  .objective_code_point    = 0xF1, /* experimental / private */
  .of_id                   = "iCPLA(ETX+alpha*QLR, RL-tunable)"
};

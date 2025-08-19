#include "contiki.h"
#include "sys/log.h"
#include "net/routing/rpl-lite/rpl.h"
#include "net/routing/rpl-lite/rpl-dag.h"
#include "net/routing/rpl-lite/rpl-neighbor.h"
#include "net/routing/rpl-lite/rpl-mrhof.h"
#include "rpl-icpla.h"

#define LOG_MODULE "RPL-ICPLA"
#define LOG_LEVEL LOG_LEVEL_INFO

/* ETX in fixed-point (128 = 1.0) */
static inline uint16_t etx_of_link(const rpl_link_metric_t *lm) {
  return lm->metric;
}

/* m = ETX + α·QLR  (all in fixed-point / 128) */
static uint16_t combined_link_metric(uint16_t etx_fp) {
  uint16_t qlr_fp = icpla_current_qlr_fp(); /* [0..128] */
  uint32_t add = ((uint32_t)RPL_ICPLA_ALPHA * (uint32_t)qlr_fp + (RPL_ETX_DIVISOR/2)) / RPL_ETX_DIVISOR;
  uint32_t res = (uint32_t)etx_fp + add;
  return (uint16_t)(res > 0xFFFF ? 0xFFFF : res);
}

/* --- OF hooks --- */
static void reset(rpl_instance_t *instance) {(void)instance;}
static void neighbor_state_changed(rpl_parent_t *p, int known, rpl_link_metric_t *lm) {(void)p;(void)known;(void)lm;}

static rpl_parent_t *best_parent(rpl_parent_t *p1, rpl_parent_t *p2) {
  if(!p1) return p2; if(!p2) return p1;
  uint16_t m1 = combined_link_metric(etx_of_link(&p1->link_metric));
  uint16_t m2 = combined_link_metric(etx_of_link(&p2->link_metric));
  if(m1 + RPL_ICPLA_PARENT_SWITCH_THRESHOLD < m2) return p1;
  if(m2 + RPL_ICPLA_PARENT_SWITCH_THRESHOLD < m1) return p2;

  /* tie-breakers */
  if(p1->rank < p2->rank) return p1;
  if(p2->rank < p1->rank) return p2;
  uint16_t e1 = etx_of_link(&p1->link_metric), e2 = etx_of_link(&p2->link_metric);
  if(e1 < e2) return p1;
  if(e2 < e1) return p2;
  return rpl_select_parent_by_dag_rank(p1, p2);
}

static rpl_rank_t calculate_rank(rpl_parent_t *p, rpl_rank_t base_rank) {
  if(p == NULL) return base_rank == 0 ? RPL_MIN_HOPRANKINC : base_rank;
  uint16_t link_fp = combined_link_metric(etx_of_link(&p->link_metric));
  uint32_t inc = (uint32_t)link_fp * (uint32_t)RPL_MIN_HOPRANKINC;
  inc = (inc + (RPL_ETX_DIVISOR/2)) / RPL_ETX_DIVISOR;
  if(base_rank == 0) base_rank = p->rank;
  uint32_t new_rank = (uint32_t)base_rank + inc;
  return (rpl_rank_t)(new_rank > RPL_INFINITE_RANK ? RPL_INFINITE_RANK : new_rank);
}

static uint16_t parent_link_metric(rpl_parent_t *p) {
  return combined_link_metric(etx_of_link(&p->link_metric));
}

static void update_metric_container(rpl_instance_t *instance) {
  /* Keep ETX as MC for interoperability */
  rpl_set_default_metric_container(instance);
}

/* Exported OF */
rpl_of_t rpl_icpla = {
  .reset = reset,
  .neighbor_state_changed = neighbor_state_changed,
  .best_parent = best_parent,
  .calculate_rank = calculate_rank,
  .parent_link_metric = parent_link_metric,
  .update_metric_container = update_metric_container,
  .ocp = RPL_OCP_MRHOF, /* same OCP as MRHOF */
  .name = "iCPLA(ETX+α·QLR)"
};

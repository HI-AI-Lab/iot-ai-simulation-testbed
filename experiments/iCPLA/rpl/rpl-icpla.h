#ifndef RPL_ICPLA_H_
#define RPL_ICPLA_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Init small RL controller */
void icpla_init(uint16_t node_id);

/* Feed observations once per period */
void icpla_observe(float qlr, float etx, float ecr, float e2e_ms, uint32_t now_ms);

/* RL-chosen shedding knob: 0..5 => 0..50% drop-in-tenths */
uint8_t icpla_get_drop_prob_tenths(void);

/* ---- Runtime α control (milli-units: 350 => 0.350) ---- */
void     icpla_set_alpha_milli(uint16_t a_milli);
uint16_t icpla_get_alpha_milli(void);

/* Optional bias if you later map to parent selection */
float icpla_get_rank_bias(void);

#ifdef __cplusplus
}
#endif
#endif /* RPL_ICPLA_H_ */

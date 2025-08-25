#include "rpl-icpla.h"
#include "random.h"
#include <string.h>

/* ------- Tiny epsilon-greedy controller -------
   Lightweight local knob; the main RL runs in Python.
   We keep an internal action 0..5 (tenths) and update with a heuristic.
*/

#define N_A   6u /* actions 0..5 tenths */
static uint8_t act = 0;

static uint8_t have_last = 0;
static uint16_t last_s = 0;
static uint8_t last_a = 0;

static float eps = 0.10f;

static uint16_t alpha_milli = 300;

static inline uint16_t pack_state(float qlr, float etx, float ecr)
{
  /* Bucketize 0..1 metrics into 0..3 */
  uint16_t bq = (qlr < 0) ? 3 : (qlr > 1) ? 3 : (uint16_t)(qlr * 4.0f);
  if(bq>3) bq=3;
  uint16_t be = (etx < 0) ? 3 : (etx > 3) ? 3 : (uint16_t)(etx); /* 0..3 approx */
  uint16_t br = (ecr < 0) ? 3 : (ecr > 1) ? 3 : (uint16_t)(ecr * 4.0f);
  if(be>3) be=3; if(br>3) br=3;
  return (bq<<4) | (be<<2) | br;
}

void icpla_init(uint16_t node_id)
{
  (void)node_id;
  act = 0;
  have_last = 0;
  alpha_milli = 300;
}

/* Called periodically from app to update action knob from simple heuristic */
void icpla_observe(float qlr, float etx, float ecr, float e2e_ms, uint32_t now_ms)
{
  (void)e2e_ms; (void)now_ms;

  uint16_t s = pack_state(qlr, etx, ecr);

  /* Simple policy: higher qlr -> increase dropping; else decrease a bit */
  if(qlr > 0.4f) {
    act = (act < 5) ? act + 1 : 5;
  } else if(qlr < 0.1f) {
    act = (act > 0) ? act - 1 : 0;
  } else {
    /* small random walk for exploration */
    if((random_rand() % 100) < (uint16_t)(eps*100)) {
      if(random_rand() & 1) { if(act<5) act++; } else { if(act>0) act--; }
    }
  }

  (void)s;
}

uint8_t icpla_get_drop_prob_tenths(void){ return act; }

void     icpla_set_alpha_milli(uint16_t a_milli){ if(a_milli>1000) a_milli=1000; alpha_milli = a_milli; }
uint16_t icpla_get_alpha_milli(void){ return alpha_milli; }

/* Optional bias; for parent scoring if you later integrate */
float icpla_get_rank_bias(void){ return 1.0f - (act / 5.0f); }

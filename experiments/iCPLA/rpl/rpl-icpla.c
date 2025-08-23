#include "rpl-icpla.h"
#include <string.h>

/* ------- Tiny epsilon-greedy Q-learning controller -------
   State = bucketized (QLR, ETX, ECR)  ->  4 * 4 * 4  = 64 states
   Actions = app shedding level a in {0..5} => drop_prob_tenths = a
   Also stores runtime α (milli) pushed from Python/Root. */

#define N_Q   4u
#define N_E   4u
#define N_R   4u
#define N_S  (N_Q * N_E * N_R)
#define N_A   6u

static float Q[N_S][N_A];
static float eps = 0.10f, alpha_lr = 0.20f, gamma_ = 0.90f;

static uint8_t last_s = 0, last_a = 0, have_last = 0;
static uint8_t act = 0;
static uint16_t my_id = 0;

/* iCPLA α in milli-units (default 0.300 => 300) */
static uint16_t alpha_milli = 300;

static uint8_t bq(float x){ if(x<0.10f)return 0; if(x<0.30f)return 1; if(x<0.60f)return 2; return 3; }
static uint8_t be(float etx){ if(etx<1.5f)return 0; if(etx<2.5f)return 1; if(etx<4.0f)return 2; return 3; }
static uint8_t br(float ecr){ if(ecr<0.20f)return 0; if(ecr<0.50f)return 1; if(ecr<0.80f)return 2; return 3; }
static uint8_t make_state(float qlr, float etx, float ecr){
  return (uint8_t)((bq(qlr)*N_E + be(etx))*N_R + br(ecr));
}

/* LCG PRNG for epsilon-greedy */
static uint32_t seed32 = 1;
static uint32_t rnd32(void){ seed32 = seed32*1103515245u + 12345u; return seed32; }
static float frand01(void){ return (float)(rnd32() & 0xFFFFFF) / (float)0x1000000; }

void icpla_init(uint16_t node_id){
  memset(Q, 0, sizeof(Q));
  seed32 = 0xA5A5A5u ^ (uint32_t)node_id;
  my_id = node_id;
  act = 0; have_last = 0;
  alpha_milli = 300; /* 0.300 default */
}

static uint8_t argmax_a(uint8_t s){
  float best = Q[s][0]; uint8_t a = 0;
  for(uint8_t i=1;i<N_A;i++){ if(Q[s][i] > best){ best = Q[s][i]; a = i; } }
  return a;
}

/* reward: improve (low QLR, low ECR, low E2E). α shapes QLR emphasis */
static float compute_reward(float qlr, float ecr, float e2e_ms){
  float e2e_norm = e2e_ms <= 0 ? 0.0f : (e2e_ms / 2000.0f);
  if(e2e_norm > 1.0f) e2e_norm = 1.0f;

  float a = (float)alpha_milli / 1000.0f; /* 0..1 */
  float penalty = (0.45f + 0.35f*a) * qlr + 0.30f * ecr + 0.25f * e2e_norm;
  float r = 1.0f - penalty;
  if(r < -1.0f) r = -1.0f; if(r > 1.0f) r = 1.0f;
  return r;
}

void icpla_observe(float qlr, float etx, float ecr, float e2e_ms, uint32_t now_ms){
  (void)now_ms;
  uint8_t s = make_state(qlr, etx, ecr);

  if(have_last){
    float r = compute_reward(qlr, ecr, e2e_ms);
    float qsa = Q[last_s][last_a];
    float next_best = Q[s][argmax_a(s)];
    Q[last_s][last_a] = qsa + alpha_lr * (r + gamma_ * next_best - qsa);
  }

  act = (frand01() < eps) ? (uint8_t)(rnd32() % N_A) : argmax_a(s);
  last_s = s; last_a = act; have_last = 1;
}

uint8_t icpla_get_drop_prob_tenths(void){ return act; }

void     icpla_set_alpha_milli(uint16_t a_milli){ if(a_milli>1000) a_milli=1000; alpha_milli = a_milli; }
uint16_t icpla_get_alpha_milli(void){ return alpha_milli; }

float icpla_get_rank_bias(void){ return 1.0f - (act / 5.0f); }

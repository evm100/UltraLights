/* Shared between the common LEDC code and the per-chip fade file. */
#pragma once

#include <stdint.h>
#include "driver/ledc.h"
#include "esp_err.h"
#include "esp_timer.h"

#define UL_PWM_MODE        LEDC_LOW_SPEED_MODE
#define UL_PWM_MAX_POINTS  8

typedef struct {
    ledc_channel_t ch;
    ledc_timer_t timer;
    uint32_t hz;
    uint32_t max_duty;      /* 2^resolution */
    /* stepped fades (chips without hardware multi-fade) */
    esp_timer_handle_t step_timer;
    uint32_t steps[UL_PWM_MAX_POINTS];
    int n_steps, next_step;
    uint32_t step_ms;
} ul_pwm_ch_t;

/* Per-chip: start a fade through duties[0..n-1] (n >= 2) over fade_ms. Called with the driver lock held. */
esp_err_t ul_pwm_chip_fade(ul_pwm_ch_t *c, const uint32_t *duties, int n, uint32_t fade_ms);
/* Per-chip: stop any fade in progress (also called with the lock held). */
void ul_pwm_chip_stop(ul_pwm_ch_t *c);

/* Driver lock, for per-chip code that runs from a timer. */
void ul_pwm_lock(void);
void ul_pwm_unlock(void);

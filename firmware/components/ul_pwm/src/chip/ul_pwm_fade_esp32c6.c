/*
 * ESP32-C6: LEDC has gamma RAM (up to 16 linear ranges per fade), so a curved
 * fade through several points is one hardware multi-fade with no CPU wakeups.
 */
#include "soc/soc_caps.h"
#include "ul_pwm_priv.h"

#define FIELD_MAX ((1u << SOC_LEDC_FADE_PARAMS_BIT_WIDTH) - 1)

/* Append the ranges for one linear segment from *at to `to` over `cycles` PWM periods. */
static int add_segment(ledc_fade_param_config_t *list, int len, uint32_t *at, uint32_t to, uint32_t cycles)
{
    uint32_t from = *at;
    uint32_t delta = to > from ? to - from : from - to;
    uint32_t dir = to > from;
    if (cycles == 0) {
        cycles = 1;
    }
    if (delta == 0) {
        uint32_t steps = (cycles + FIELD_MAX - 1) / FIELD_MAX;
        steps = steps > FIELD_MAX ? FIELD_MAX : steps;
        uint32_t per = cycles / steps;
        list[len++] = (ledc_fade_param_config_t){.dir = 1, .cycle_num = per ? per : 1, .scale = 0, .step_num = steps};
        return len;
    }
    uint32_t scale = (delta + FIELD_MAX - 1) / FIELD_MAX;
    uint32_t steps = delta / scale;
    uint32_t per = cycles / steps;
    per = per < 1 ? 1 : per > FIELD_MAX ? FIELD_MAX : per;
    list[len++] = (ledc_fade_param_config_t){.dir = dir, .cycle_num = per, .scale = scale, .step_num = steps};
    uint32_t moved = scale * steps;
    uint32_t rest = delta - moved;
    if (rest) {
        list[len++] = (ledc_fade_param_config_t){.dir = dir, .cycle_num = 1, .scale = rest, .step_num = 1};
    }
    *at = to;
    return len;
}

esp_err_t ul_pwm_chip_fade(ul_pwm_ch_t *c, const uint32_t *duties, int n, uint32_t fade_ms)
{
    ledc_fade_param_config_t list[SOC_LEDC_GAMMA_CURVE_FADE_RANGE_MAX];
    uint32_t start = ledc_get_duty(UL_PWM_MODE, c->ch);
    uint32_t at = start;
    uint32_t cycles = (uint32_t)((uint64_t)fade_ms * c->hz / 1000u / (uint32_t)n);
    int len = 0;
    for (int i = 0; i < n && len + 2 <= SOC_LEDC_GAMMA_CURVE_FADE_RANGE_MAX; i++) {
        len = add_segment(list, len, &at, duties[i], cycles);
    }
    if (at != duties[n - 1]) {
        /* Out of ranges: finish with one plain fade. */
        return ledc_set_fade_time_and_start(UL_PWM_MODE, c->ch, duties[n - 1], fade_ms, LEDC_FADE_NO_WAIT);
    }
    return ledc_set_multi_fade_and_start(UL_PWM_MODE, c->ch, start, list, (uint32_t)len, LEDC_FADE_NO_WAIT);
}

void ul_pwm_chip_stop(ul_pwm_ch_t *c)
{
    ledc_fade_stop(UL_PWM_MODE, c->ch);
}

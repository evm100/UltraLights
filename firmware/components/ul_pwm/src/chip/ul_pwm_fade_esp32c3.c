/*
 * ESP32-C3: LEDC has linear hardware fades only (no gamma RAM), so a curved
 * fade is a chain of linear hardware fades, one per point, started by a timer.
 * Each segment still runs in hardware; the CPU only wakes between segments.
 */
#include "ul_pwm_priv.h"

static void step(void *arg)
{
    ul_pwm_ch_t *c = arg;
    ul_pwm_lock();
    if (c->next_step < c->n_steps) {
        ledc_set_fade_time_and_start(UL_PWM_MODE, c->ch, c->steps[c->next_step], c->step_ms, LEDC_FADE_NO_WAIT);
        c->next_step++;
    }
    if (c->next_step >= c->n_steps) {
        esp_timer_stop(c->step_timer);
    }
    ul_pwm_unlock();
}

esp_err_t ul_pwm_chip_fade(ul_pwm_ch_t *c, const uint32_t *duties, int n, uint32_t fade_ms)
{
    if (!c->step_timer) {
        const esp_timer_create_args_t ta = {.callback = step, .arg = c, .name = "ul_pwm_step"};
        esp_err_t err = esp_timer_create(&ta, &c->step_timer);
        if (err != ESP_OK) {
            return ledc_set_fade_time_and_start(UL_PWM_MODE, c->ch, duties[n - 1], fade_ms, LEDC_FADE_NO_WAIT);
        }
    }
    for (int i = 0; i < n; i++) {
        c->steps[i] = duties[i];
    }
    c->n_steps = n;
    c->step_ms = fade_ms / (uint32_t)n;
    c->next_step = 1;
    esp_err_t err = ledc_set_fade_time_and_start(UL_PWM_MODE, c->ch, duties[0], c->step_ms, LEDC_FADE_NO_WAIT);
    if (err == ESP_OK) {
        err = esp_timer_start_periodic(c->step_timer, (uint64_t)c->step_ms * 1000);
    }
    return err;
}

void ul_pwm_chip_stop(ul_pwm_ch_t *c)
{
    if (c->step_timer) {
        esp_timer_stop(c->step_timer);
    }
    c->n_steps = 0;
    ledc_fade_stop(UL_PWM_MODE, c->ch);
}

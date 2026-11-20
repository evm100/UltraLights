/* LEDC PWM channels with hardware fades. Pins and frequencies come from the board descriptor. */
#include <math.h>
#include <string.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "soc/soc_caps.h"
#include "ul_board.h"
#include "ul_core.h"
#include "ul_pwm.h"
#include "ul_pwm_priv.h"

static const char *TAG = "ul_pwm";

#define PWM_SRC_HZ 80000000u /* APB (C3) / PLL_F80M (C6): what LEDC_AUTO_CLK picks at these frequencies */

static ul_pwm_ch_t s_ch[SOC_LEDC_CHANNEL_NUM];
static int s_n;
static SemaphoreHandle_t s_lock;

void ul_pwm_lock(void) { xSemaphoreTake(s_lock, portMAX_DELAY); }
void ul_pwm_unlock(void) { xSemaphoreGive(s_lock); }

static uint32_t to_duty(const ul_pwm_ch_t *c, float v)
{
    v = v < 0 ? 0 : v > 1 ? 1 : v;
    return (uint32_t)lroundf(v * (float)c->max_duty);
}

static esp_err_t set_duty(void *ctx, const float *points, int n, uint32_t fade_ms)
{
    ul_pwm_ch_t *c = ctx;
    uint32_t duties[UL_PWM_MAX_POINTS];
    n = n > UL_PWM_MAX_POINTS ? UL_PWM_MAX_POINTS : n;
    for (int i = 0; i < n; i++) {
        duties[i] = to_duty(c, points[i]);
    }
    esp_err_t err;
    ul_pwm_lock();
    ul_pwm_chip_stop(c);
    uint32_t target = duties[n - 1];
    uint32_t now = ledc_get_duty(UL_PWM_MODE, c->ch);
    if (fade_ms == 0 || now == target) {
        err = ledc_set_duty_and_update(UL_PWM_MODE, c->ch, target, 0);
    } else if (n == 1) {
        err = ledc_set_fade_time_and_start(UL_PWM_MODE, c->ch, target, fade_ms, LEDC_FADE_NO_WAIT);
    } else {
        err = ul_pwm_chip_fade(c, duties, n, fade_ms);
    }
    ul_pwm_unlock();
    return err;
}

static const ul_channel_ops_t OPS = {.set_duty = set_duty};

/* Bits of duty resolution for a frequency: as many as the clock allows, within the timer width. */
static ledc_timer_bit_t resolution_for(uint32_t hz)
{
    uint32_t bits = 0;
    while (bits < SOC_LEDC_TIMER_BIT_WIDTH && (PWM_SRC_HZ / hz) >> (bits + 1)) {
        bits++;
    }
    return (ledc_timer_bit_t)bits;
}

static esp_err_t ul_pwm_init(void)
{
    const ul_board_desc_t *b = ul_board_get();
    s_lock = xSemaphoreCreateMutex();
    uint32_t timer_hz[SOC_LEDC_TIMER_NUM] = {0};
    ledc_timer_bit_t timer_bits[SOC_LEDC_TIMER_NUM] = {0};
    int n_timers = 0;
    bool fade_installed = false;
    for (int i = 0; i < b->n_channels; i++) {
        const ul_channel_desc_t *d = &b->ch[i];
        if (d->type != UL_CH_PWM) {
            continue;
        }
        if (s_n >= SOC_LEDC_CHANNEL_NUM) {
            ul_core_report_error("too_many_pwm", d->id, "no LEDC channel left");
            continue;
        }
        int t;
        for (t = 0; t < n_timers && timer_hz[t] != d->hz; t++) {
        }
        ul_pwm_ch_t *c = &s_ch[s_n];
        if (t == n_timers) {
            if (n_timers == SOC_LEDC_TIMER_NUM) {
                ul_core_report_error("too_many_pwm_freqs", d->id, "no LEDC timer left for %lu Hz", (unsigned long)d->hz);
                continue;
            }
            ledc_timer_bit_t bits = resolution_for(d->hz);
            esp_err_t err = ESP_FAIL;
            for (; bits >= 4 && err != ESP_OK; bits--) {
                ledc_timer_config_t tc = {
                    .speed_mode = UL_PWM_MODE, .duty_resolution = bits, .timer_num = (ledc_timer_t)t,
                    .freq_hz = d->hz, .clk_cfg = LEDC_AUTO_CLK,
                };
                err = ledc_timer_config(&tc);
                if (err == ESP_OK) {
                    break;
                }
            }
            if (err != ESP_OK) {
                ul_core_report_error("pwm_timer", d->id, "can't run %lu Hz", (unsigned long)d->hz);
                continue;
            }
            timer_bits[n_timers] = bits;
            timer_hz[n_timers++] = d->hz;
            ESP_LOGI(TAG, "timer %d: %lu Hz, %d bits", t, (unsigned long)d->hz, bits);
        }
        if (!fade_installed) {
            ESP_ERROR_CHECK(ledc_fade_func_install(0));
            fade_installed = true;
        }
        c->ch = (ledc_channel_t)s_n;
        c->timer = (ledc_timer_t)t;
        c->hz = d->hz;
        c->max_duty = 1u << timer_bits[t];
        ledc_channel_config_t cc = {
            .gpio_num = d->gpio, .speed_mode = UL_PWM_MODE, .channel = c->ch, .timer_sel = c->timer,
            .duty = 0, .hpoint = 0, .flags.output_invert = d->invert,
        };
        esp_err_t err = ledc_channel_config(&cc);
        if (err != ESP_OK) {
            ul_core_report_error("pwm_channel", d->id, "LEDC setup failed: %s", esp_err_to_name(err));
            continue;
        }
        s_n++;
        ESP_ERROR_CHECK(ul_core_register_channel(d->id, &OPS, c));
        ESP_LOGI(TAG, "%s: GPIO%d, LEDC ch %d, timer %d", d->id, d->gpio, c->ch, c->timer);
    }
    return ESP_OK;
}

UL_MODULE_REGISTER(pwm, "ul_pwm", UL_STAGE_DRIVER, 10, ul_pwm_init)

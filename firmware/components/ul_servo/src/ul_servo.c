/*
 * Servo channels on LEDC at 50 Hz. One press is a small state machine on an
 * esp_timer: press angle for hold_ms, rest angle for release_ms, then the
 * signal stops and ul_core hears that the press is done. Pins and angles come
 * from the board descriptor.
 */
#include <string.h>
#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"
#include "ul_board.h"
#include "ul_core.h"
#include "ul_servo.h"

static const char *TAG = "ul_servo";

#define SERVO_MODE      LEDC_LOW_SPEED_MODE
#define SERVO_HZ        50
#define SERVO_BITS      LEDC_TIMER_14_BIT
#define SERVO_TIMER     ((ledc_timer_t)(SOC_LEDC_TIMER_NUM - 1))
#define FRAME_US        (1000000 / SERVO_HZ)

typedef enum { IDLE, SETTLING, PRESSING, RELEASING } phase_t;

typedef struct {
    char id[UL_KEY_SZ];
    ledc_channel_t ch;
    uint32_t press_duty, rest_duty;
    uint32_t hold_ms, release_ms;
    esp_timer_handle_t timer;
    int64_t due_us;         /* when the running phase ends */
    phase_t phase;
} servo_t;

static servo_t s_servo[SOC_LEDC_CHANNEL_NUM];
static int s_n;
/* Serializes press() (any task) with tick() (esp_timer task). Never held while calling into
 * ul_core: ul_core calls press() with its own lock held. */
static SemaphoreHandle_t s_lock;

static void start_phase(servo_t *s, phase_t phase, uint32_t ms)
{
    s->phase = phase;
    s->due_us = esp_timer_get_time() + (int64_t)ms * 1000;
    esp_timer_stop(s->timer);
    esp_timer_start_once(s->timer, (uint64_t)ms * 1000);
}

static uint32_t duty_for(int deg)
{
    uint32_t us = CONFIG_UL_SERVO_MIN_US + (uint32_t)(CONFIG_UL_SERVO_MAX_US - CONFIG_UL_SERVO_MIN_US) * (uint32_t)deg / 180u;
    return (uint32_t)(((uint64_t)us << SERVO_BITS) / FRAME_US);
}

/* ledc_set_duty + update, not the fade API: servos never fade, and it needs no fade service. */
static void out(const servo_t *s, uint32_t duty)
{
    ledc_set_duty(SERVO_MODE, s->ch, duty);
    ledc_update_duty(SERVO_MODE, s->ch);
}

static void stop_signal(const servo_t *s)
{
#if CONFIG_UL_SERVO_DETACH
    out(s, 0); /* pin held low: the servo stops driving and goes quiet */
#else
    (void)s;
#endif
}

static void tick(void *arg)
{
    servo_t *s = arg;
    bool done = false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    /* A callback from a phase that press() replaced while it waited for the lock: ignore it. */
    if (esp_timer_get_time() + 1000 < s->due_us) {
        xSemaphoreGive(s_lock);
        return;
    }
    switch (s->phase) {
    case PRESSING:
        out(s, s->rest_duty);
        start_phase(s, RELEASING, s->release_ms);
        break;
    case RELEASING:
        stop_signal(s);
        s->phase = IDLE;
        done = true;
        break;
    case SETTLING:
        stop_signal(s);
        s->phase = IDLE;
        break;
    case IDLE:
        break;
    }
    xSemaphoreGive(s_lock);
    if (done) {
        ul_core_press_done(s->id, true); /* may start the next press from here */
    }
}

static esp_err_t press(void *ctx)
{
    servo_t *s = ctx;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s->phase == PRESSING || s->phase == RELEASING) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_STATE; /* ul_core never asks twice; never cut a press short */
    }
    /* A boot settle may still be running: the press takes over its timer. */
    out(s, s->press_duty);
    start_phase(s, PRESSING, s->hold_ms);
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

static const ul_channel_ops_t OPS = {.press = press};

static esp_err_t ul_servo_init(void)
{
    const ul_board_desc_t *b = ul_board_get();
    bool timer_ready = false;
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) {
        return ESP_ERR_NO_MEM;
    }
    for (int i = 0; i < b->n_channels; i++) {
        const ul_channel_desc_t *d = &b->ch[i];
        if (d->type != UL_CH_SERVO) {
            continue;
        }
        if (s_n >= SOC_LEDC_CHANNEL_NUM) {
            ul_core_report_error("too_many_pwm", d->id, "no LEDC channel left for a servo");
            continue;
        }
        if (!timer_ready) {
            ledc_timer_config_t tc = {
                .speed_mode = SERVO_MODE, .duty_resolution = SERVO_BITS, .timer_num = SERVO_TIMER,
                .freq_hz = SERVO_HZ, .clk_cfg = LEDC_AUTO_CLK,
            };
            esp_err_t err = ledc_timer_config(&tc);
            if (err != ESP_OK) {
                ul_core_report_error("servo_timer", d->id, "LEDC timer: %s", esp_err_to_name(err));
                return err;
            }
            timer_ready = true;
            ESP_LOGI(TAG, "timer %d: %d Hz, 14 bits", SERVO_TIMER, SERVO_HZ);
        }
        servo_t *s = &s_servo[s_n];
        memset(s, 0, sizeof(*s));
        snprintf(s->id, sizeof(s->id), "%s", d->id);
        s->ch = (ledc_channel_t)(SOC_LEDC_CHANNEL_NUM - 1 - s_n);
        s->press_duty = duty_for(d->press_deg);
        s->rest_duty = duty_for(d->rest_deg);
        s->hold_ms = d->hold_ms;
        s->release_ms = d->release_ms;
        const esp_timer_create_args_t ta = {.callback = tick, .arg = s, .name = "ul_servo"};
        esp_err_t err = esp_timer_create(&ta, &s->timer);
        if (err == ESP_OK) {
            /* Start with no pulses (pin low), then go to rest: the arm never jumps toward the button. */
            ledc_channel_config_t cc = {
                .gpio_num = d->gpio, .speed_mode = SERVO_MODE, .channel = s->ch, .timer_sel = SERVO_TIMER,
                .duty = 0, .hpoint = 0,
            };
            err = ledc_channel_config(&cc);
        }
        if (err != ESP_OK) {
            ul_core_report_error("servo_channel", d->id, "GPIO%d: %s", d->gpio, esp_err_to_name(err));
            continue;
        }
        s_n++;
        out(s, s->rest_duty);
        start_phase(s, SETTLING, s->release_ms);
        ESP_ERROR_CHECK(ul_core_register_channel(d->id, &OPS, s));
        ESP_LOGI(TAG, "%s: GPIO%d, LEDC ch %d, press %d deg, rest %d deg, hold %d ms, %d modes", d->id, d->gpio,
                 s->ch, d->press_deg, d->rest_deg, d->hold_ms, d->n_modes);
    }
    return ESP_OK;
}

UL_MODULE_REGISTER(servo, "ul_servo", UL_STAGE_DRIVER, 15, ul_servo_init)

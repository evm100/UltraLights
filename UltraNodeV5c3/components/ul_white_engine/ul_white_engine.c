#include "ul_white_engine.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "string.h"
#include "effects_white/effect.h"
#include "cJSON.h"

static const char* TAG = "ul_white";

typedef struct {
    bool enabled;
    bool power;
    int pwm_hz;
    int gpio;
    int ledc_ch;
    uint8_t brightness;
    const white_effect_t* eff;
    int frame_idx;
} white_ch_t;

static white_ch_t s_ch[4];
static int s_count = 0;

static const white_effect_t* find_eff(const char* name) {
    int n=0; const white_effect_t* t = ul_white_get_effects(&n);
    for (int i=0;i<n;i++) if (strcmp(t[i].name, name)==0) return &t[i];
    return NULL;
}

static void setup_ledc_channel(int ch, int gpio, int freq_hz)
{
    ledc_timer_config_t tcfg = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_12_BIT,
        .freq_hz = freq_hz,
        .clk_cfg = LEDC_AUTO_CLK
    };
    ledc_timer_config(&tcfg);
    ledc_channel_config_t ccfg = {
        .gpio_num = gpio,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = ch,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
        .hpoint = 0
    };
    ledc_channel_config(&ccfg);
}

static void ch_init(int idx, bool enabled, int gpio, int ledc_ch, int pwm_hz) {
    s_ch[idx].enabled = enabled;
    s_ch[idx].gpio = gpio;
    s_ch[idx].ledc_ch = ledc_ch;
    s_ch[idx].pwm_hz = pwm_hz;
    s_ch[idx].power = true;
    s_ch[idx].brightness = 0;
    int n=0; const white_effect_t* t = ul_white_get_effects(&n);
    s_ch[idx].eff = &t[0];
    s_ch[idx].frame_idx = 0;
    if (enabled) setup_ledc_channel(ledc_ch, gpio, pwm_hz);
    if (enabled) s_count++;
}

static void white_task(void*)
{
    // Use the dedicated smoothing rate for periodic updates. If the
    // configured rate is faster than the system tick, fall back to 1 tick
    // so the task still yields and avoids assertion failures.
    TickType_t period_ticks = pdMS_TO_TICKS(1000) / CONFIG_UL_WHITE_SMOOTH_HZ;
    if (period_ticks == 0) {
        period_ticks = 1;
    }
    TickType_t last_wake = xTaskGetTickCount();
    int n = 0; ul_white_get_effects(&n); // ensure linked
    while (1) {
        for (int i=0;i<4;i++) {
            if (!s_ch[i].enabled) continue;
            uint8_t v = s_ch[i].eff->render_brightness(s_ch[i].frame_idx++);
            // brightness setpoint overrides effect amplitude
            v = s_ch[i].brightness;
            if (!s_ch[i].power) v = 0;
            int duty = (v * ((1<<12)-1)) / 255;
            ledc_set_duty(LEDC_LOW_SPEED_MODE, s_ch[i].ledc_ch, duty);
            ledc_update_duty(LEDC_LOW_SPEED_MODE, s_ch[i].ledc_ch);
        }
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

void ul_white_engine_start(void)
{
    // Channel 0..3 from Kconfig (only enabling those flagged)
#if CONFIG_UL_WHT0_ENABLED
    ch_init(0, true, CONFIG_UL_WHT0_GPIO, CONFIG_UL_WHT0_LEDC_CH, CONFIG_UL_WHT0_PWM_HZ);
#else
    ch_init(0, false, 0, 0, 0);
#endif
#if CONFIG_UL_WHT1_ENABLED
    ch_init(1, true, CONFIG_UL_WHT1_GPIO, CONFIG_UL_WHT1_LEDC_CH, CONFIG_UL_WHT1_PWM_HZ);
#else
    ch_init(1, false, 0, 0, 0);
#endif
#if CONFIG_UL_WHT2_ENABLED
    ch_init(2, true, CONFIG_UL_WHT2_GPIO, CONFIG_UL_WHT2_LEDC_CH, CONFIG_UL_WHT2_PWM_HZ);
#else
    ch_init(2, false, 0, 0, 0);
#endif
#if CONFIG_UL_WHT3_ENABLED
    ch_init(3, true, CONFIG_UL_WHT3_GPIO, CONFIG_UL_WHT3_LEDC_CH, CONFIG_UL_WHT3_PWM_HZ);
#else
    ch_init(3, false, 0, 0, 0);
#endif
    // Execute on the same core as the WS2812 engine but slightly lower
    // priority so the pixel refresh always wins. Core 0 remains free for
    // networking and other tasks.
    xTaskCreatePinnedToCore(white_task, "white200hz", 4096, NULL, 23, NULL, 1);
}

static white_ch_t* get_ch(int ch) {
    if (ch < 0 || ch > 3) return NULL;
    if (!s_ch[ch].enabled) return NULL;
    return &s_ch[ch];
}

bool ul_white_set_effect(int ch, const char* name) {
    white_ch_t* c = get_ch(ch);
    if (!c) return false;
    const white_effect_t* e = find_eff(name);
    if (!e) return false;
    c->eff = e;
    if (c->eff->init) c->eff->init();
    return true;
}

bool ul_white_set_brightness(int ch, uint8_t bri) {
    white_ch_t* c = get_ch(ch);
    if (!c) return false;
    c->brightness = bri;
    return true;
}

bool ul_white_power(int ch, bool on) {
    white_ch_t* c = get_ch(ch);
    if (!c) return false;
    c->power = on;
    return true;
}

void ul_white_apply_json(cJSON* root) {
    if (!root) return;
    int ch = 0;
    cJSON* jch = cJSON_GetObjectItem(root, "channel");
    if (jch && cJSON_IsNumber(jch)) ch = jch->valueint;

    cJSON* jeff = cJSON_GetObjectItem(root, "effect");
    if (jeff && cJSON_IsString(jeff)) {
        if (!ul_white_set_effect(ch, jeff->valuestring)) {
            ESP_LOGW(TAG, "unknown white effect: %s", jeff->valuestring);
        }
    }

    cJSON* jbri = cJSON_GetObjectItem(root, "brightness");
    if (jbri && cJSON_IsNumber(jbri)) {
        int bri = jbri->valueint;
        if (bri < 0) bri = 0;
        if (bri > 255) bri = 255;
        ul_white_set_brightness(ch, (uint8_t)bri);
    }
}

int ul_white_get_channel_count(void) { return s_count; }

bool ul_white_get_status(int ch, ul_white_ch_status_t* out) {
    white_ch_t* c = get_ch(ch);
    if (!out) return false;
    if (!c) { memset(out, 0, sizeof(*out)); return false; }
    out->enabled = c->enabled;
    out->power = c->power;
    out->brightness = c->brightness;
    out->pwm_hz = c->pwm_hz;
    out->gpio = c->gpio;
    strncpy(out->effect, c->eff ? c->eff->name : "unknown", sizeof(out->effect)-1);
    out->effect[sizeof(out->effect)-1] = 0;
    return true;
}

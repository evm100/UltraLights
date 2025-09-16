#include "ul_rgb_engine.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "string.h"
#include "cJSON.h"
#include "ul_task.h"
#include "ul_common_effects.h"
#include "effects_rgb/effect.h"

static const char* TAG = "ul_rgb";

typedef struct {
    int gpio;
    ledc_mode_t mode;
    ledc_channel_t channel;
    bool configured;
} rgb_channel_t;

typedef struct {
    bool enabled;
    int pwm_hz;
    rgb_channel_t channel[3];
    uint8_t brightness;
    const rgb_effect_t* eff;
    int frame_idx;
    uint8_t solid_color[3];
    uint8_t last_color[3];
} rgb_strip_t;

static rgb_strip_t s_strips[4];
static int s_strip_count = 0;
static TaskHandle_t s_rgb_task = NULL;
static int s_current_strip = 0;

int ul_rgb_effect_current_strip(void) { return s_current_strip; }

static ledc_mode_t decode_mode(int mode_cfg) {
#if defined(LEDC_HIGH_SPEED_MODE)
    return mode_cfg ? LEDC_HIGH_SPEED_MODE : LEDC_LOW_SPEED_MODE;
#else
    (void)mode_cfg;
    return LEDC_LOW_SPEED_MODE;
#endif
}

static void setup_ledc_channel(rgb_channel_t* ch, int gpio, int ledc_ch, int mode_cfg, int freq_hz) {
    ch->gpio = gpio;
    ch->channel = (ledc_channel_t)ledc_ch;
    ch->mode = decode_mode(mode_cfg);
    ch->configured = true;

    ledc_timer_config_t timer = {
        .speed_mode = ch->mode,
        .timer_num = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_12_BIT,
        .freq_hz = freq_hz,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t config = {
        .gpio_num = gpio,
        .speed_mode = ch->mode,
        .channel = ch->channel,
        .intr_type = LEDC_INTR_DISABLE,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
        .hpoint = 0,
    };
    ledc_channel_config(&config);
}

static void disable_channel(rgb_channel_t* ch) {
    ch->gpio = -1;
    ch->channel = 0;
    ch->mode = LEDC_LOW_SPEED_MODE;
    ch->configured = false;
}

static const rgb_effect_t* find_effect(const char* name) {
    if (!name) return NULL;
    int n = 0;
    const rgb_effect_t* tbl = ul_rgb_get_effects(&n);
    for (int i = 0; i < n; ++i) {
        if (strcmp(tbl[i].name, name) == 0) return &tbl[i];
    }
    return NULL;
}

static rgb_strip_t* get_strip(int idx) {
    if (idx < 0 || idx >= (int)(sizeof(s_strips) / sizeof(s_strips[0]))) return NULL;
    if (!s_strips[idx].enabled) return NULL;
    return &s_strips[idx];
}

void ul_rgb_set_solid_rgb(int strip, uint8_t r, uint8_t g, uint8_t b) {
    rgb_strip_t* s = get_strip(strip);
    if (!s) return;
    s->solid_color[0] = r;
    s->solid_color[1] = g;
    s->solid_color[2] = b;
}

void ul_rgb_get_solid_rgb(int strip, uint8_t* r, uint8_t* g, uint8_t* b) {
    rgb_strip_t* s = get_strip(strip);
    if (!s) return;
    if (r) *r = s->solid_color[0];
    if (g) *g = s->solid_color[1];
    if (b) *b = s->solid_color[2];
}

static void strip_init(int idx,
                       bool enabled,
                       int pwm_hz,
                       int ledc_mode,
                       int r_gpio, int r_ch,
                       int g_gpio, int g_ch,
                       int b_gpio, int b_ch) {
    rgb_strip_t* s = &s_strips[idx];
    memset(s, 0, sizeof(*s));
    s->enabled = enabled;
    s->pwm_hz = pwm_hz;
    s->brightness = 255;
    if (!enabled) {
        disable_channel(&s->channel[0]);
        disable_channel(&s->channel[1]);
        disable_channel(&s->channel[2]);
        return;
    }

    setup_ledc_channel(&s->channel[0], r_gpio, r_ch, ledc_mode, pwm_hz);
    setup_ledc_channel(&s->channel[1], g_gpio, g_ch, ledc_mode, pwm_hz);
    setup_ledc_channel(&s->channel[2], b_gpio, b_ch, ledc_mode, pwm_hz);

    int effect_count = 0;
    const rgb_effect_t* tbl = ul_rgb_get_effects(&effect_count);
    if (effect_count > 0) {
        s->eff = &tbl[0];
        if (s->eff->init) s->eff->init();
    }
    s_strip_count++;

    ESP_LOGI(TAG, "RGB strip %d enabled (R=%d,G=%d,B=%d)", idx, r_gpio, g_gpio, b_gpio);
}

static void set_channel_value(rgb_channel_t* ch, uint8_t value) {
    if (!ch->configured) return;
    int duty = (value * ((1 << 12) - 1)) / 255;
    ledc_set_duty(ch->mode, ch->channel, duty);
    ledc_update_duty(ch->mode, ch->channel);
}

static void rgb_task(void* arg) {
    (void)arg;
    TickType_t period_ticks = pdMS_TO_TICKS(1000) / CONFIG_UL_RGB_SMOOTH_HZ;
    if (period_ticks == 0) period_ticks = 1;
    TickType_t last_wake = xTaskGetTickCount();

    int n = 0;
    ul_rgb_get_effects(&n); // ensure table linked

    while (1) {
        for (int i = 0; i < 4; ++i) {
            rgb_strip_t* s = get_strip(i);
            if (!s) continue;
            s_current_strip = i;
            uint8_t rgb[3] = {0, 0, 0};
            if (s->eff && s->eff->render) {
                s->eff->render(i, rgb, s->frame_idx++);
            }
            memcpy(s->last_color, rgb, sizeof(rgb));
#if CONFIG_UL_GAMMA_ENABLE
            for (int c = 0; c < 3; ++c) {
                rgb[c] = ul_gamma8(rgb[c]);
            }
#endif
            for (int c = 0; c < 3; ++c) {
                uint8_t value = (uint8_t)((rgb[c] * s->brightness) / 255);
                set_channel_value(&s->channel[c], value);
            }
        }
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

void ul_rgb_engine_start(void) {
    memset(s_strips, 0, sizeof(s_strips));
    s_strip_count = 0;

#if CONFIG_UL_RGB0_ENABLED
    strip_init(0, true, CONFIG_UL_RGB0_PWM_HZ, CONFIG_UL_RGB0_LEDC_MODE,
               CONFIG_UL_RGB0_R_GPIO, CONFIG_UL_RGB0_R_LEDC_CH,
               CONFIG_UL_RGB0_G_GPIO, CONFIG_UL_RGB0_G_LEDC_CH,
               CONFIG_UL_RGB0_B_GPIO, CONFIG_UL_RGB0_B_LEDC_CH);
#endif
#if CONFIG_UL_RGB1_ENABLED
    strip_init(1, true, CONFIG_UL_RGB1_PWM_HZ, CONFIG_UL_RGB1_LEDC_MODE,
               CONFIG_UL_RGB1_R_GPIO, CONFIG_UL_RGB1_R_LEDC_CH,
               CONFIG_UL_RGB1_G_GPIO, CONFIG_UL_RGB1_G_LEDC_CH,
               CONFIG_UL_RGB1_B_GPIO, CONFIG_UL_RGB1_B_LEDC_CH);
#endif
#if CONFIG_UL_RGB2_ENABLED
    strip_init(2, true, CONFIG_UL_RGB2_PWM_HZ, CONFIG_UL_RGB2_LEDC_MODE,
               CONFIG_UL_RGB2_R_GPIO, CONFIG_UL_RGB2_R_LEDC_CH,
               CONFIG_UL_RGB2_G_GPIO, CONFIG_UL_RGB2_G_LEDC_CH,
               CONFIG_UL_RGB2_B_GPIO, CONFIG_UL_RGB2_B_LEDC_CH);
#endif
#if CONFIG_UL_RGB3_ENABLED
    strip_init(3, true, CONFIG_UL_RGB3_PWM_HZ, CONFIG_UL_RGB3_LEDC_MODE,
               CONFIG_UL_RGB3_R_GPIO, CONFIG_UL_RGB3_R_LEDC_CH,
               CONFIG_UL_RGB3_G_GPIO, CONFIG_UL_RGB3_G_LEDC_CH,
               CONFIG_UL_RGB3_B_GPIO, CONFIG_UL_RGB3_B_LEDC_CH);
#endif

    if (s_strip_count > 0) {
        ul_task_create(rgb_task, "rgb_smooth", 4096, NULL, 23, &s_rgb_task, 1);
    } else {
        ESP_LOGI(TAG, "RGB engine started with no enabled strips");
    }
}

void ul_rgb_engine_stop(void) {
    if (s_rgb_task) {
        vTaskDelete(s_rgb_task);
        s_rgb_task = NULL;
    }
    for (int i = 0; i < 4; ++i) {
        if (!s_strips[i].enabled) continue;
        for (int c = 0; c < 3; ++c) {
            set_channel_value(&s_strips[i].channel[c], 0);
        }
        s_strips[i].enabled = false;
    }
    s_strip_count = 0;
}

bool ul_rgb_set_effect(int strip, const char* name) {
    rgb_strip_t* s = get_strip(strip);
    if (!s) return false;
    const rgb_effect_t* eff = find_effect(name);
    if (!eff) return false;
    s->eff = eff;
    s->frame_idx = 0;
    if (s->eff && s->eff->init) s->eff->init();
    return true;
}

bool ul_rgb_set_brightness(int strip, uint8_t bri) {
    rgb_strip_t* s = get_strip(strip);
    if (!s) return false;
    s->brightness = bri;
    return true;
}

void ul_rgb_apply_json(cJSON* root) {
    if (!root) return;
    int strip = 0;
    cJSON* jstrip = cJSON_GetObjectItem(root, "strip");
    if (jstrip && cJSON_IsNumber(jstrip)) strip = jstrip->valueint;

    cJSON* jbri = cJSON_GetObjectItem(root, "brightness");
    if (jbri && cJSON_IsNumber(jbri)) {
        int bri = jbri->valueint;
        if (bri < 0) bri = 0;
        if (bri > 255) bri = 255;
        ul_rgb_set_brightness(strip, (uint8_t)bri);
    }

    const char* effect = NULL;
    cJSON* jeffect = cJSON_GetObjectItem(root, "effect");
    if (jeffect && cJSON_IsString(jeffect)) {
        effect = jeffect->valuestring;
        if (!ul_rgb_set_effect(strip, effect)) {
            ESP_LOGW(TAG, "Unknown RGB effect: %s", effect);
            effect = NULL;
        }
    }

    cJSON* jparams = cJSON_GetObjectItem(root, "params");
    if (effect && jparams && cJSON_IsArray(jparams)) {
        rgb_strip_t* s = get_strip(strip);
        if (s && s->eff && s->eff->apply_params) {
            s->eff->apply_params(strip, jparams);
        }
    }
}

int ul_rgb_get_strip_count(void) { return s_strip_count; }

bool ul_rgb_get_status(int strip, ul_rgb_strip_status_t* out) {
    if (!out) return false;
    memset(out, 0, sizeof(*out));
    rgb_strip_t* s = get_strip(strip);
    if (!s) return false;
    out->enabled = true;
    out->brightness = s->brightness;
    out->pwm_hz = s->pwm_hz;
    if (s->eff && s->eff->name) {
        strncpy(out->effect, s->eff->name, sizeof(out->effect) - 1);
        out->effect[sizeof(out->effect) - 1] = 0;
    } else {
        strncpy(out->effect, "unknown", sizeof(out->effect) - 1);
        out->effect[sizeof(out->effect) - 1] = 0;
    }
    for (int c = 0; c < 3; ++c) {
        out->channel[c].gpio = s->channel[c].gpio;
        out->channel[c].ledc_ch = s->channel[c].channel;
#if defined(LEDC_HIGH_SPEED_MODE)
        out->channel[c].ledc_mode = (s->channel[c].mode == LEDC_HIGH_SPEED_MODE) ? 1 : 0;
#else
        out->channel[c].ledc_mode = 0;
#endif
        out->color[c] = s->last_color[c];
    }
    return true;
}

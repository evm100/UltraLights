#include "ul_ws_engine.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "led_strip.h"
#include "led_strip_rmt.h"
#include "led_strip_rmt.h"
#include "led_strip_spi.h"
#include "led_strip_types.h"
#include <string.h>
#include <stdlib.h>
#include "effects_ws/effect.h"
#include "ul_common_effects.h"

static const char* TAG = "ul_ws";

typedef struct {
    const ws_effect_t* eff;
    uint8_t solid_r, solid_g, solid_b;
    uint8_t brightness; // 0..255
    bool power;
    int frame_idx;
    int pixels;
    led_strip_handle_t handle;
    uint8_t* frame; // rgb * pixels
} ws_strip_t;

static ws_strip_t s_strips[4];

static const ws_effect_t* find_effect_by_name(const char* name) {
    int n=0;
    const ws_effect_t* tbl = ul_ws_get_effects(&n);
    for (int i=0;i<n;i++) {
        if (strcmp(tbl[i].name, name)==0) return &tbl[i];
    }
    return NULL;
}

static void init_strip(int idx, int gpio, int pixels, bool enabled) {
    if (!enabled) { s_strips[idx].pixels = 0; return; }
    led_strip_config_t strip_config = {
        .strip_gpio_num = gpio,
        .max_leds = pixels,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000, // 10MHz
        .mem_block_symbols = 0,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strips[idx].handle));
    s_strips[idx].pixels = pixels;
    s_strips[idx].frame = (uint8_t*)heap_caps_malloc(pixels*3, MALLOC_CAP_8BIT);
    memset(s_strips[idx].frame, 0, pixels*3);
    // defaults
    int n=0; const ws_effect_t* tbl = ul_ws_get_effects(&n);
    s_strips[idx].eff = &tbl[0]; // solid
    s_strips[idx].solid_r = s_strips[idx].solid_g = s_strips[idx].solid_b = 255;
    s_strips[idx].brightness = 255;
    s_strips[idx].power = true;
    s_strips[idx].frame_idx = 0;
}

static void apply_brightness(uint8_t* f, int count, uint8_t bri) {
    if (bri == 255) return;
    for (int i=0;i<count;i++) {
        int v = (f[i] * bri) / 255;
        f[i] = (uint8_t)v;
    }
}

static void render_one(ws_strip_t* s) {
    if (!s->pixels || !s->handle) return;
    // Produce frame
    memset(s->frame, 0, s->pixels*3);
    if (s->eff && s->eff->render) {
        s->eff->render(s->frame, s->pixels, s->frame_idx++);
    }
    // If solid: override with solid color
    if (s->eff && strcmp(s->eff->name, "solid")==0) {
        for (int i=0;i<s->pixels;i++) {
            s->frame[3*i+0] = s->solid_r;
            s->frame[3*i+1] = s->solid_g;
            s->frame[3*i+2] = s->solid_b;
        }
    }
#if CONFIG_UL_GAMMA_ENABLE
    for (int i=0;i<s->pixels;i++) {
        s->frame[3*i+0] = ul_gamma8(s->frame[3*i+0]);
        s->frame[3*i+1] = ul_gamma8(s->frame[3*i+1]);
        s->frame[3*i+2] = ul_gamma8(s->frame[3*i+2]);
    }
#endif
    apply_brightness(s->frame, s->pixels*3, s->brightness);
    // Power gating
    if (!s->power) {
        memset(s->frame, 0, s->pixels*3);
    }
    // Push to device
    for (int i=0;i<s->pixels;i++) {
        led_strip_set_pixel(s->handle, i, s->frame[3*i+0], s->frame[3*i+1], s->frame[3*i+2]);
    }
    led_strip_refresh(s->handle);
}

static void ws_task(void*)
{
    const TickType_t period_ticks = pdMS_TO_TICKS(1000 / CONFIG_UL_WS2812_FPS);
    TickType_t last_wake = xTaskGetTickCount();

    const int frame_us = 1000000 / CONFIG_UL_WS2812_FPS;
    while (1) {
#if CONFIG_UL_WS0_ENABLED
        render_one(&s_strips[0]);
#endif
#if CONFIG_UL_WS1_ENABLED
        render_one(&s_strips[1]);
#endif
#if CONFIG_UL_WS2_ENABLED
        render_one(&s_strips[2]);
#endif
#if CONFIG_UL_WS3_ENABLED
        render_one(&s_strips[3]);
#endif
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

void ul_ws_engine_start(void)
{
#if CONFIG_UL_WS0_ENABLED
    init_strip(0, CONFIG_UL_WS0_GPIO, CONFIG_UL_WS0_PIXELS, true);
#else
    init_strip(0, 0, 0, false);
#endif
#if CONFIG_UL_WS1_ENABLED
    init_strip(1, CONFIG_UL_WS1_GPIO, CONFIG_UL_WS1_PIXELS, true);
#else
    init_strip(1, 0, 0, false);
#endif
#if CONFIG_UL_WS2_ENABLED
    init_strip(2, CONFIG_UL_WS2_GPIO, CONFIG_UL_WS2_PIXELS, true);
#else
    init_strip(2, 0, 0, false);
#endif
#if CONFIG_UL_WS3_ENABLED
    init_strip(3, CONFIG_UL_WS3_GPIO, CONFIG_UL_WS3_PIXELS, true);
#else
    init_strip(3, 0, 0, false);
#endif
    xTaskCreatePinnedToCore(ws_task, "ws60fps", 6144, NULL, 8, NULL, 1);
}


// ---- Control & Status API ----

static ws_strip_t* get_strip(int idx) {
    if (idx < 0 || idx > 3) return NULL;
    if (s_strips[idx].pixels <= 0) return NULL;
    return &s_strips[idx];
}

bool ul_ws_set_effect(int strip, const char* name) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return false;
    const ws_effect_t* e = find_effect_by_name(name);
    if (!e) return false;
    s->eff = e;
    if (s->eff->init) s->eff->init();
    return true;
}

void ul_ws_set_solid_rgb(int strip, uint8_t r, uint8_t g, uint8_t b) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return;
    s->solid_r = r; s->solid_g = g; s->solid_b = b;
}

void ul_ws_set_brightness(int strip, uint8_t bri) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return;
    s->brightness = bri;
}

void ul_ws_power(int strip, bool on) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return;
    s->power = on;
}


int ul_ws_get_strip_count(void) {
    int n=0;
#if CONFIG_UL_WS0_ENABLED
    if (s_strips[0].pixels>0) n++;
#endif
#if CONFIG_UL_WS1_ENABLED
    if (s_strips[1].pixels>0) n++;
#endif
#if CONFIG_UL_WS2_ENABLED
    if (s_strips[2].pixels>0) n++;
#endif
#if CONFIG_UL_WS3_ENABLED
    if (s_strips[3].pixels>0) n++;
#endif
    return n;
}

bool ul_ws_get_status(int idx, ul_ws_strip_status_t* out) {
    if (!out) return false;
    ws_strip_t* s = get_strip(idx);
    if (!s) { memset(out,0,sizeof(*out)); return false; }
    out->enabled = true;
    out->power = s->power;
    out->brightness = s->brightness;
    out->pixels = s->pixels;
    out->gpio = 0; // not tracked in led_strip
    out->fps = CONFIG_UL_WS2812_FPS;
    strncpy(out->effect, s->eff ? s->eff->name : "unknown", sizeof(out->effect)-1);
    out->effect[sizeof(out->effect)-1]=0;
    out->color[0]=s->solid_r; out->color[1]=s->solid_g; out->color[2]=s->solid_b;
    return true;
}

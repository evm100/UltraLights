#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <stdbool.h>
#include <stdint.h>

static uint8_t s_color[2][3];
static bool s_initialized;

#define COLOR_SWELL_DURATION_MS 3000
#define COLOR_SWELL_MIN_FRAMES 256

static inline bool valid_strip(int strip) {
    return strip >= 0 && strip < 2;
}

static inline uint8_t clamp_u8(int value) {
    if (value < 0) return 0;
    if (value > 255) return 255;
    return (uint8_t)value;
}

static void ensure_initialized(void) {
    if (s_initialized) return;
    for (int i = 0; i < 2; ++i) {
        s_color[i][0] = 255;
        s_color[i][1] = 255;
        s_color[i][2] = 255;
    }
    s_initialized = true;
}

void color_swell_init(void) {
    ensure_initialized();
}

static int compute_total_frames(void) {
    int frames = (COLOR_SWELL_DURATION_MS * CONFIG_UL_WS2812_FPS) / 1000;
    if (frames < COLOR_SWELL_MIN_FRAMES) {
        frames = COLOR_SWELL_MIN_FRAMES;
    }
    return frames;
}

static uint8_t read_color_component(const cJSON* item, uint8_t fallback) {
    if (!item || !cJSON_IsNumber(item)) return fallback;
    return clamp_u8(item->valueint);
}

void color_swell_apply_params(int strip, const cJSON* params) {
    ensure_initialized();
    if (!valid_strip(strip)) return;
    if (!params || !cJSON_IsArray(params)) return;

    s_color[strip][0] = read_color_component(cJSON_GetArrayItem(params, 0), s_color[strip][0]);
    s_color[strip][1] = read_color_component(cJSON_GetArrayItem(params, 1), s_color[strip][1]);
    s_color[strip][2] = read_color_component(cJSON_GetArrayItem(params, 2), s_color[strip][2]);
}

void color_swell_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    ensure_initialized();
    int strip = ul_ws_effect_current_strip();
    if (!valid_strip(strip)) return;

    int frames = compute_total_frames();
    int value = 255;
    if (frame_idx <= 0) {
        value = 0;
    } else if (frame_idx < frames - 1) {
        int64_t scaled = ((int64_t)frame_idx * 255) / (frames - 1);
        if (scaled < 0) {
            scaled = 0;
        }
        if (scaled > 255) {
            scaled = 255;
        }
        value = (int)scaled;
    }

    for (int i = 0; i < pixels; ++i) {
        frame_rgb[3 * i + 0] = (uint8_t)((s_color[strip][0] * value) / 255);
        frame_rgb[3 * i + 1] = (uint8_t)((s_color[strip][1] * value) / 255);
        frame_rgb[3 * i + 2] = (uint8_t)((s_color[strip][2] * value) / 255);
    }
}

#endif

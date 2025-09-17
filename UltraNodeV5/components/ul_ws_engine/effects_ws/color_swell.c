#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <stdbool.h>

static uint8_t s_color[2][3];
static uint8_t s_start[2];
static uint8_t s_end[2];
static int s_frames[2];
static int s_progress[2];
static bool s_initialized;

static inline bool valid_strip(int strip) {
    return strip >= 0 && strip < 2;
}

static inline uint8_t clamp_u8(int value) {
    if (value < 0) return 0;
    if (value > 255) return 255;
    return (uint8_t)value;
}

static inline int clamp_frames(int frames) {
    if (frames < 1) return 1;
    return frames;
}

static void ensure_initialized(void) {
    if (s_initialized) return;
    for (int i = 0; i < 2; ++i) {
        s_color[i][0] = 255;
        s_color[i][1] = 255;
        s_color[i][2] = 255;
        s_start[i] = 0;
        s_end[i] = 255;
        s_frames[i] = 1;
        s_progress[i] = 0;
    }
    s_initialized = true;
}

void color_swell_init(void) {
    ensure_initialized();
}

static uint8_t read_color_component(const cJSON* item) {
    if (!item || !cJSON_IsNumber(item)) return 0;
    return clamp_u8(item->valueint);
}

static uint8_t read_brightness(const cJSON* item, uint8_t fallback) {
    if (!item || !cJSON_IsNumber(item)) return fallback;
    return clamp_u8(item->valueint);
}

static int read_frames(const cJSON* item, int fallback) {
    if (!item || !cJSON_IsNumber(item)) return clamp_frames(fallback);
    int ms = item->valueint;
    if (ms < 0) ms = 0;
    int frames = (ms * CONFIG_UL_WS2812_FPS) / 1000;
    return clamp_frames(frames);
}

void color_swell_apply_params(int strip, const cJSON* params) {
    ensure_initialized();
    if (!valid_strip(strip)) return;
    if (!params || !cJSON_IsArray(params)) return;

    s_color[strip][0] = read_color_component(cJSON_GetArrayItem(params, 0));
    s_color[strip][1] = read_color_component(cJSON_GetArrayItem(params, 1));
    s_color[strip][2] = read_color_component(cJSON_GetArrayItem(params, 2));
    s_start[strip] = read_brightness(cJSON_GetArrayItem(params, 3), s_start[strip]);
    s_end[strip] = read_brightness(cJSON_GetArrayItem(params, 4), s_end[strip]);
    s_frames[strip] = read_frames(cJSON_GetArrayItem(params, 5), s_frames[strip]);
    s_progress[strip] = 0;
}

void color_swell_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    ensure_initialized();
    int strip = ul_ws_effect_current_strip();
    if (!valid_strip(strip)) return;

    uint8_t start = s_start[strip];
    uint8_t end = s_end[strip];
    int frames = s_frames[strip];
    int progress = s_progress[strip];

    int value = end;
    if (progress < frames) {
        float t = frames ? (float)progress / (float)frames : 1.0f;
        value = (int)(start + (end - start) * t + 0.5f);
        value = clamp_u8(value);
        s_progress[strip]++;
    }

    for (int i = 0; i < pixels; ++i) {
        frame_rgb[3 * i + 0] = (uint8_t)((s_color[strip][0] * value) / 255);
        frame_rgb[3 * i + 1] = (uint8_t)((s_color[strip][1] * value) / 255);
        frame_rgb[3 * i + 2] = (uint8_t)((s_color[strip][2] * value) / 255);
    }
}

#endif

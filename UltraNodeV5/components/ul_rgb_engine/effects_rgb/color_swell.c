#include "sdkconfig.h"

#if CONFIG_UL_RGB0_ENABLED || CONFIG_UL_RGB1_ENABLED || CONFIG_UL_RGB2_ENABLED || CONFIG_UL_RGB3_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <stdbool.h>

#define RGB_STRIP_MAX 4

static uint8_t s_color[RGB_STRIP_MAX][3];
static uint8_t s_start[RGB_STRIP_MAX];
static uint8_t s_end[RGB_STRIP_MAX];
static int s_frames[RGB_STRIP_MAX];
static int s_progress[RGB_STRIP_MAX];
static bool s_initialized;

static inline bool valid_strip(int strip) {
    return strip >= 0 && strip < RGB_STRIP_MAX;
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
    for (int i = 0; i < RGB_STRIP_MAX; ++i) {
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

void rgb_color_swell_init(void) {
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
    int frames = (ms * CONFIG_UL_RGB_SMOOTH_HZ) / 1000;
    return clamp_frames(frames);
}

void rgb_color_swell_apply_params(int strip, const cJSON* params) {
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

void rgb_color_swell_render(int strip, uint8_t out_rgb[3], int frame_idx) {
    (void)frame_idx;
    ensure_initialized();
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

    out_rgb[0] = (uint8_t)((s_color[strip][0] * value) / 255);
    out_rgb[1] = (uint8_t)((s_color[strip][1] * value) / 255);
    out_rgb[2] = (uint8_t)((s_color[strip][2] * value) / 255);
}

#endif

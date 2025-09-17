#include "sdkconfig.h"

#if CONFIG_UL_WHT0_ENABLED || CONFIG_UL_WHT1_ENABLED || CONFIG_UL_WHT2_ENABLED || CONFIG_UL_WHT3_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <stdbool.h>

static uint8_t s_start[4];
static uint8_t s_end[4];
static int s_frames[4];
static int s_progress[4];
static bool s_initialized;

void white_swell_init(void) {
    if (!s_initialized) {
        for (int i = 0; i < 4; ++i) {
            s_start[i] = 0;
            s_end[i] = 255;
            s_frames[i] = 1;
            s_progress[i] = 0;
        }
        s_initialized = true;
    }
}

uint8_t white_swell_render(int frame_idx) {
    (void)frame_idx;
    int ch = ul_white_effect_current_channel();
    if (ch < 0 || ch > 3) return 0;
    if (s_progress[ch] < s_frames[ch]) {
        float t = s_frames[ch] ? (float)s_progress[ch] / (float)s_frames[ch] : 1.0f;
        int v = (int)(s_start[ch] + (s_end[ch] - s_start[ch]) * t + 0.5f);
        s_progress[ch]++;
        if (v < 0) v = 0;
        if (v > 255) v = 255;
        return (uint8_t)v;
    }
    return s_end[ch];
}

void white_swell_apply_params(int ch, const cJSON* params) {
    if (ch < 0 || ch > 3) return;
    if (!params || !cJSON_IsArray(params)) return;
    const cJSON* p0 = cJSON_GetArrayItem(params, 0);
    const cJSON* p1 = cJSON_GetArrayItem(params, 1);
    const cJSON* p2 = cJSON_GetArrayItem(params, 2);
    if (p0 && cJSON_IsNumber(p0)) {
        int x = p0->valueint;
        if (x < 0) x = 0;
        if (x > 255) x = 255;
        s_start[ch] = (uint8_t)x;
    }
    if (p1 && cJSON_IsNumber(p1)) {
        int y = p1->valueint;
        if (y < 0) y = 0;
        if (y > 255) y = 255;
        s_end[ch] = (uint8_t)y;
    }
    if (p2 && cJSON_IsNumber(p2)) {
        int ms = p2->valueint;
        if (ms < 0) ms = 0;
        int f = (ms * CONFIG_UL_WHITE_SMOOTH_HZ) / 1000;
        if (f < 1) f = 1;
        s_frames[ch] = f;
    }
    s_progress[ch] = 0;
}

#endif


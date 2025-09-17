#include "sdkconfig.h"

#if CONFIG_UL_WHT0_ENABLED || CONFIG_UL_WHT1_ENABLED || CONFIG_UL_WHT2_ENABLED || CONFIG_UL_WHT3_ENABLED

#include "effect.h"
#include <math.h>
#include "cJSON.h"

static int s_period_ms = 1000;

void white_breathe_init(void) {
    s_period_ms = 1000;
}

static int period_frames(void) {
    int frames = (s_period_ms * CONFIG_UL_WHITE_SMOOTH_HZ) / 1000;
    if (frames < 1) frames = 1;
    return frames;
}

uint8_t white_breathe_render(int frame_idx) {
    int frames = period_frames();
    float t = (frame_idx % frames) / (float)frames;
    float v = 0.5f * (1.0f - cosf(2.0f * 3.1415926f * t));
    if (v < 0) v = 0;
    if (v > 1) v = 1;
    return (uint8_t)(v * 255.0f + 0.5f);
}

void white_breathe_apply_params(int ch, const cJSON* params) {
    (void)ch;
    if (!params || !cJSON_IsArray(params)) return;
    const cJSON* p = cJSON_GetArrayItem(params, 0);
    if (p && cJSON_IsNumber(p)) {
        int ms = p->valueint;
        if (ms < 100) ms = 100;
        s_period_ms = ms;
    }
}

#endif


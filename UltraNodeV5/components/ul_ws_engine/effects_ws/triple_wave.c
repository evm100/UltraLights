#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define NUM_STRIPS 2

#define NUM_WAVES 3

typedef struct {
    uint8_t r, g, b;
    float wavelength;
    float freq;
} wave_cfg_t;

static wave_cfg_t s_waves[NUM_STRIPS][NUM_WAVES];


void triple_wave_init(void) {
    // no initialization required
}

void triple_wave_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip >= NUM_STRIPS) return;
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < NUM_WAVES * 5) return;
    for (int w = 0; w < NUM_WAVES; ++w) {
        s_waves[strip][w].r = (uint8_t)cJSON_GetArrayItem(params, w*5 + 0)->valueint;
        s_waves[strip][w].g = (uint8_t)cJSON_GetArrayItem(params, w*5 + 1)->valueint;
        s_waves[strip][w].b = (uint8_t)cJSON_GetArrayItem(params, w*5 + 2)->valueint;
        s_waves[strip][w].wavelength = (float)cJSON_GetArrayItem(params, w*5 + 3)->valuedouble;
        s_waves[strip][w].freq = (float)cJSON_GetArrayItem(params, w*5 + 4)->valuedouble;
    }
}

void triple_wave_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    int strip = ul_ws_effect_current_strip();
    if (strip < 0 || strip >= NUM_STRIPS) return;
    for (int i = 0; i < pixels; ++i) {
        float r = 0.0f, g = 0.0f, b = 0.0f;
        for (int w = 0; w < NUM_WAVES; ++w) {
            wave_cfg_t* cfg = &s_waves[strip][w];
            if (cfg->wavelength <= 0.0f) continue;
            float pos = (float)i / cfg->wavelength;
            float phase = 2.0f * (float)M_PI * (pos + frame_idx * cfg->freq);
            float intensity = (sinf(phase) + 1.0f) * 0.5f; // 0..1
            r += intensity * cfg->r;
            g += intensity * cfg->g;
            b += intensity * cfg->b;
        }
        if (r > 255.0f) r = 255.0f;
        if (g > 255.0f) g = 255.0f;
        if (b > 255.0f) b = 255.0f;
        frame_rgb[3*i]   = (uint8_t)r;
        frame_rgb[3*i+1] = (uint8_t)g;
        frame_rgb[3*i+2] = (uint8_t)b;
    }
}

#endif

// Improved Spacewaves effect implementation.
// The effect renders three sine waves with configurable colors that sweep
// across the LED strip at different wavelengths and speeds. Parameters are
// passed as an array of RGB triplets – one per wave.

#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "cJSON.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define MAX_STRIPS 2
#define NUM_WAVES 3

typedef struct {
    uint8_t r;
    uint8_t g;
    uint8_t b;
} wave_cfg_t;

static wave_cfg_t s_waves[MAX_STRIPS][NUM_WAVES];

void spacewaves_init(void) {
    // Provide sensible defaults so the effect works even without params.
    for (int s = 0; s < MAX_STRIPS; ++s) {
        s_waves[s][0] = (wave_cfg_t){255, 0, 0};   // red
        s_waves[s][1] = (wave_cfg_t){0, 255, 0};   // green
        s_waves[s][2] = (wave_cfg_t){0, 0, 255};   // blue
    }
}

void spacewaves_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip >= MAX_STRIPS) return;
    if (!params || !cJSON_IsArray(params)) return;

    int count = cJSON_GetArraySize(params);
    for (int w = 0; w < NUM_WAVES; ++w) {
        int base = w * 3;
        if (base + 2 >= count) break;  // not enough values
        s_waves[strip][w].r = (uint8_t)cJSON_GetArrayItem(params, base + 0)->valueint;
        s_waves[strip][w].g = (uint8_t)cJSON_GetArrayItem(params, base + 1)->valueint;
        s_waves[strip][w].b = (uint8_t)cJSON_GetArrayItem(params, base + 2)->valueint;
    }
}

void spacewaves_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    // Predefined wavelengths and temporal frequencies for each wave.
    static const float wavelengths[NUM_WAVES] = {30.f, 45.f, 60.f};
    static const float freqs[NUM_WAVES] = {0.20f, 0.15f, 0.10f};

    int strip = ul_ws_effect_current_strip();
    if (strip < 0 || strip >= MAX_STRIPS) return;

    for (int i = 0; i < pixels; ++i) {
        float r = 0.f, g = 0.f, b = 0.f;
        for (int w = 0; w < NUM_WAVES; ++w) {
            wave_cfg_t* cfg = &s_waves[strip][w];
            float pos = (float)i / wavelengths[w];
            float phase = 2.f * (float)M_PI * (pos + frame_idx * freqs[w]);
            float intensity = (sinf(phase) + 1.f) * 0.5f; // 0..1
            r += intensity * cfg->r;
            g += intensity * cfg->g;
            b += intensity * cfg->b;
        }
        if (r > 255.f) r = 255.f;
        if (g > 255.f) g = 255.f;
        if (b > 255.f) b = 255.f;
        frame_rgb[3*i + 0] = (uint8_t)r;
        frame_rgb[3*i + 1] = (uint8_t)g;
        frame_rgb[3*i + 2] = (uint8_t)b;
    }
}

#endif

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
} wave_color_t;

static wave_color_t s_colors[NUM_STRIPS][NUM_WAVES];

void spacewaves_init(void) {
    // no initialization needed
}

void spacewaves_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip >= NUM_STRIPS) return;
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < NUM_WAVES * 3) return;

    for (int w = 0; w < NUM_WAVES; ++w) {
        s_colors[strip][w].r = (uint8_t)cJSON_GetArrayItem(params, w*3 + 0)->valueint;
        s_colors[strip][w].g = (uint8_t)cJSON_GetArrayItem(params, w*3 + 1)->valueint;
        s_colors[strip][w].b = (uint8_t)cJSON_GetArrayItem(params, w*3 + 2)->valueint;
    }
}

void spacewaves_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    static const float wavelengths[NUM_WAVES] = {30.f, 45.f, 60.f};
    static const float freqs[NUM_WAVES] = {0.20f, 0.15f, 0.10f};

    int strip = ul_ws_effect_current_strip();
    if (strip < 0 || strip >= NUM_STRIPS) return;

    for (int i = 0; i < pixels; ++i) {
        float r = 0.f, g = 0.f, b = 0.f;
        for (int w = 0; w < NUM_WAVES; ++w) {
            wave_color_t* c = &s_colors[strip][w];
            float pos = (float)i / wavelengths[w];
            float phase = 2.f * (float)M_PI * (pos + frame_idx * freqs[w]);
            float intensity = (sinf(phase) + 1.f) * 0.5f;
            r += intensity * c->r;
            g += intensity * c->g;
            b += intensity * c->b;
        }
        if (r > 255.f) r = 255.f;
        if (g > 255.f) g = 255.f;
        if (b > 255.f) b = 255.f;
        frame_rgb[3*i + 0] = (uint8_t)r;
        frame_rgb[3*i + 1] = (uint8_t)g;
        frame_rgb[3*i + 2] = (uint8_t)b;
    }
}

#include "effect.h"
#include "cJSON.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define NUM_WAVES 3

typedef struct {
    uint8_t r, g, b;
    float wavelength;
    float frequency;
} wave_cfg_t;

/* Fixed configuration for the three color waves */
static const wave_cfg_t s_waves[NUM_WAVES] = {
    {255, 0,   0, 30.0f, 0.10f}, // red wave
    {0,   255, 0, 45.0f, 0.07f}, // green wave
    {0,   0, 255, 60.0f, 0.05f}  // blue wave
};

void triple_wave_init(void) {
    // no initialization required
}

void triple_wave_apply_params(int strip, const cJSON* params) {
    (void)strip;
    (void)params; // effect has fixed parameters
}

void triple_wave_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    for (int i = 0; i < pixels; ++i) {
        float r = 0.0f, g = 0.0f, b = 0.0f;
        for (int w = 0; w < NUM_WAVES; ++w) {
            float pos = (float)i / s_waves[w].wavelength;
            float phase = 2.0f * (float)M_PI * (pos + frame_idx * s_waves[w].frequency);
            float intensity = (sinf(phase) + 1.0f) * 0.5f; // 0..1
            r += intensity * s_waves[w].r;
            g += intensity * s_waves[w].g;
            b += intensity * s_waves[w].b;
        }
        if (r > 255.0f) r = 255.0f;
        if (g > 255.0f) g = 255.0f;
        if (b > 255.0f) b = 255.0f;
        frame_rgb[3*i]   = (uint8_t)r;
        frame_rgb[3*i+1] = (uint8_t)g;
        frame_rgb[3*i+2] = (uint8_t)b;
    }
}

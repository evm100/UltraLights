#include "effect.h"
#include "ul_ws_engine.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

void triple_wave_init(void) {
    // no-op
}

void triple_wave_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    int strip = ul_ws_effect_current_strip();
    const ul_ws_wave_cfg_t* waves = ul_ws_triple_wave_get(strip);
    if (!waves) return;

    for (int i = 0; i < pixels; ++i) {
        float pos = (float)i / (float)pixels;
        float r = 0.0f, g = 0.0f, b = 0.0f;
        for (int w = 0; w < 3; ++w) {
            float phase = 2.0f * (float)M_PI * (waves[w].freq * pos + frame_idx * waves[w].velocity);
            float s = (sinf(phase) + 1.0f) * 0.5f; // 0..1
            r += s * waves[w].r;
            g += s * waves[w].g;
            b += s * waves[w].b;
        }
        if (r > 255.0f) r = 255.0f;
        if (g > 255.0f) g = 255.0f;
        if (b > 255.0f) b = 255.0f;
        frame_rgb[3*i+0] = (uint8_t)r;
        frame_rgb[3*i+1] = (uint8_t)g;
        frame_rgb[3*i+2] = (uint8_t)b;
    }
}


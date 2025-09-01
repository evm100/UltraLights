#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

typedef struct {
    uint8_t r, g, b;
    float freq;
    float velocity;
} wave_cfg_t;

static wave_cfg_t s_waves[2][3];

void triple_wave_init(void) { }

void triple_wave_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip > 1) return;
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) != 3) return;
    for (int i = 0; i < 3; ++i) {
        cJSON* jw = cJSON_GetArrayItem(params, i);
        cJSON* jhex = cJSON_GetObjectItem(jw, "hex");
        cJSON* jfreq = cJSON_GetObjectItem(jw, "freq");
        cJSON* jvel = cJSON_GetObjectItem(jw, "velocity");
        if (!jhex || !cJSON_IsString(jhex) || !jfreq || !cJSON_IsNumber(jfreq) || !jvel || !cJSON_IsNumber(jvel)) {
            continue;
        }
        ul_ws_hex_to_rgb(jhex->valuestring, &s_waves[strip][i].r, &s_waves[strip][i].g, &s_waves[strip][i].b);
        s_waves[strip][i].freq = (float)jfreq->valuedouble;
        s_waves[strip][i].velocity = (float)jvel->valuedouble;
    }
}

void triple_wave_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    int strip = ul_ws_effect_current_strip();
    const wave_cfg_t* waves = s_waves[strip];
    for (int i = 0; i < pixels; ++i) {
        float pos = (float)i / (float)pixels;
        float r = 0.0f, g = 0.0f, b = 0.0f;
        for (int w = 0; w < 3; ++w) {
            float phase = 2.0f * (float)M_PI * (waves[w].freq * pos + frame_idx * waves[w].velocity);
            float s = (sinf(phase) + 1.0f) * 0.5f;
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

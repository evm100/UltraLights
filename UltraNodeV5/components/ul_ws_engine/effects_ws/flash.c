#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"

static uint8_t s_color1[2][3];
static uint8_t s_color2[2][3];

void flash_init(void) { }

void flash_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip > 1) return;
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < 6) return;
    for (int i = 0; i < 3; ++i) {
        s_color1[strip][i] = (uint8_t)cJSON_GetArrayItem(params, i)->valueint;
        s_color2[strip][i] = (uint8_t)cJSON_GetArrayItem(params, i+3)->valueint;
    }
}

void flash_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    int strip = ul_ws_effect_current_strip();
    uint8_t* c = ((frame_idx / 10) % 2) ? s_color2[strip] : s_color1[strip];
    for (int i = 0; i < pixels; ++i) {
        frame_rgb[3*i+0] = c[0];
        frame_rgb[3*i+1] = c[1];
        frame_rgb[3*i+2] = c[2];
    }
}

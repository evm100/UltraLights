#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"

void solid_init(void) { (void)0; }

void solid_apply_params(int strip, const cJSON* params) {
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < 3) return;

    uint8_t r = (uint8_t)cJSON_GetArrayItem(params, 0)->valueint;
    uint8_t g = (uint8_t)cJSON_GetArrayItem(params, 1)->valueint;
    uint8_t b = (uint8_t)cJSON_GetArrayItem(params, 2)->valueint;

    ul_ws_set_solid_rgb(strip, r, g, b);
}

void solid_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    int strip = ul_ws_effect_current_strip();
    uint8_t r, g, b;
    ul_ws_get_solid_rgb(strip, &r, &g, &b);
    for (int i = 0; i < pixels; ++i) {
        frame_rgb[3*i+0] = r;
        frame_rgb[3*i+1] = g;
        frame_rgb[3*i+2] = b;
    }
}

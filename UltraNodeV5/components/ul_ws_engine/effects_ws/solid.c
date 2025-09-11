#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"
#include <stdlib.h>

void solid_init(void) { (void)0; }

void solid_apply_params(int strip, const cJSON* params) {
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) == 0) return;
    uint8_t r = 0, g = 0, b = 0;

    const cJSON* first = cJSON_GetArrayItem(params, 0);
    if (cJSON_IsString(first)) {
        const char* s = first->valuestring;
        if (s && s[0] == '#') ++s;
        unsigned long v = strtoul(s, NULL, 16);
        r = (v >> 16) & 0xFF;
        g = (v >> 8) & 0xFF;
        b = v & 0xFF;
    } else if (cJSON_GetArraySize(params) >= 3) {
        r = (uint8_t)cJSON_GetArrayItem(params, 0)->valueint;
        g = (uint8_t)cJSON_GetArrayItem(params, 1)->valueint;
        b = (uint8_t)cJSON_GetArrayItem(params, 2)->valueint;
    } else {
        return;
    }

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

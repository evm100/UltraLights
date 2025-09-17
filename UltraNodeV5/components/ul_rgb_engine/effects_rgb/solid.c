#include "sdkconfig.h"

#if CONFIG_UL_RGB0_ENABLED || CONFIG_UL_RGB1_ENABLED || CONFIG_UL_RGB2_ENABLED || CONFIG_UL_RGB3_ENABLED

#include "effect.h"
#include "ul_rgb_engine.h"
#include "cJSON.h"

void rgb_solid_init(void) { (void)0; }

static uint8_t read_color_component(const cJSON* item) {
    if (!item || !cJSON_IsNumber(item)) return 0;
    int v = item->valueint;
    if (v < 0) v = 0;
    if (v > 255) v = 255;
    return (uint8_t)v;
}

void rgb_solid_apply_params(int strip, const cJSON* params) {
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < 3) return;
    uint8_t r = read_color_component(cJSON_GetArrayItem(params, 0));
    uint8_t g = read_color_component(cJSON_GetArrayItem(params, 1));
    uint8_t b = read_color_component(cJSON_GetArrayItem(params, 2));
    ul_rgb_set_solid_rgb(strip, r, g, b);
}

void rgb_solid_render(int strip, uint8_t out_rgb[3], int frame_idx) {
    (void)frame_idx;
    uint8_t r = 0, g = 0, b = 0;
    ul_rgb_get_solid_rgb(strip, &r, &g, &b);
    out_rgb[0] = r;
    out_rgb[1] = g;
    out_rgb[2] = b;
}

#endif

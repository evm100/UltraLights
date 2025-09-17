#include "sdkconfig.h"
#include "effect.h"
#include <stddef.h>

#if CONFIG_UL_RGB0_ENABLED || CONFIG_UL_RGB1_ENABLED || CONFIG_UL_RGB2_ENABLED || CONFIG_UL_RGB3_ENABLED

void rgb_solid_init(void);
void rgb_solid_render(int strip, uint8_t out_rgb[3], int frame_idx);
void rgb_solid_apply_params(int strip, const cJSON* params);
void rgb_color_swell_init(void);
void rgb_color_swell_render(int strip, uint8_t out_rgb[3], int frame_idx);
void rgb_color_swell_apply_params(int strip, const cJSON* params);

static const rgb_effect_t effects[] = {
    {"solid", rgb_solid_init, rgb_solid_render, rgb_solid_apply_params},
    {"color_swell", rgb_color_swell_init, rgb_color_swell_render, rgb_color_swell_apply_params},
};

const rgb_effect_t* ul_rgb_get_effects(int* count) {
    if (count) *count = sizeof(effects) / sizeof(effects[0]);
    return effects;
}

#else

const rgb_effect_t* ul_rgb_get_effects(int* count) {
    if (count) *count = 0;
    return NULL;
}

#endif

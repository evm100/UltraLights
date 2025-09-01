#include "effect.h"

void white_breathe_init(void);
uint8_t white_breathe_render(int frame_idx);
void white_breathe_apply_params(int ch, const cJSON* params);

void white_solid_init(void);
uint8_t white_solid_render(int frame_idx);

static const white_effect_t effects[] = {
    {"solid", white_solid_init, white_solid_render, NULL},
    {"breathe", white_breathe_init, white_breathe_render, white_breathe_apply_params},
};

const white_effect_t* ul_white_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}


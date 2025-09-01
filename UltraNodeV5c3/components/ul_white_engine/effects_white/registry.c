#include "effect.h"

void breathe_init(void);
uint8_t breathe_render(int frame_idx);
void breathe_apply_params(int ch, const cJSON* params);

static const white_effect_t effects[] = {
    {"breathe", breathe_init, breathe_render, breathe_apply_params},
};

const white_effect_t* ul_white_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}


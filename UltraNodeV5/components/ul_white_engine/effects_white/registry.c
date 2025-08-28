#include "effect.h"

uint8_t graceful_on_render(int frame_idx);
uint8_t graceful_off_render(int frame_idx);
uint8_t motion_swell_render(int frame_idx);
uint8_t day_night_curve_render(int frame_idx);
uint8_t blink_render(int frame_idx);

static void noop(void){}

static const white_effect_t effects[] = {
    {"graceful_on", noop, graceful_on_render},
    {"graceful_off", noop, graceful_off_render},
    {"motion_swell", noop, motion_swell_render},
    {"day_night_curve", noop, day_night_curve_render},
    {"blink", noop, blink_render},
};

const white_effect_t* ul_white_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}

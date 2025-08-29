#pragma once
#include <stdint.h>
typedef struct {
    const char* name;
    void (*init)(void);
    // value per strip (0..255) rendered at 200 Hz; real engine maps to LEDC duty
    uint8_t (*render_brightness)(int frame_idx);
} white_effect_t;
const white_effect_t* ul_white_get_effects(int* count);

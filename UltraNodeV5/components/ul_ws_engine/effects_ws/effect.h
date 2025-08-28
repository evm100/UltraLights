#pragma once
#include <stdint.h>
typedef struct {
    const char* name;
    void (*init)(void);
    void (*render)(uint8_t* frame_rgb, int pixels, int frame_idx);
} ws_effect_t;

const ws_effect_t* ul_ws_get_effects(int* count);

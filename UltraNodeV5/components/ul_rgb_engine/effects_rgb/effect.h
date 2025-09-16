#pragma once
#include <stdint.h>

typedef struct cJSON cJSON;

typedef struct {
    const char* name;
    void (*init)(void);
    void (*render)(int strip, uint8_t out_rgb[3], int frame_idx);
    void (*apply_params)(int strip, const cJSON* params);
} rgb_effect_t;

const rgb_effect_t* ul_rgb_get_effects(int* count);
int ul_rgb_effect_current_strip(void);

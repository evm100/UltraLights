#pragma once
#include <stdint.h>
typedef struct cJSON cJSON;

typedef struct {
    const char* name;
    void (*init)(void);
    // Render a brightness value (0..255) for the given frame index
    uint8_t (*render)(int frame_idx);
    // Optional parameter hook
    void (*apply_params)(int ch, const cJSON* params);
} white_effect_t;

const white_effect_t* ul_white_get_effects(int* count);
int ul_white_effect_current_channel(void);

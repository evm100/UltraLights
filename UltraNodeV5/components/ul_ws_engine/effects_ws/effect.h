#pragma once
#include <stdint.h>

typedef struct cJSON cJSON;

typedef enum {
    WS_EFFECT_TIER_STANDARD = 0,
    WS_EFFECT_TIER_PSRAM = 1,
} ws_effect_tier_t;

typedef struct {
    const char* name;
    ws_effect_tier_t tier;
    void (*init)(void);
    void (*render)(uint8_t* frame_rgb, int pixels, int frame_idx);
    void (*apply_params)(int strip, const cJSON* params);
} ws_effect_t;

const ws_effect_t* ul_ws_get_effects(int* count);
int ul_ws_effect_current_strip(void);

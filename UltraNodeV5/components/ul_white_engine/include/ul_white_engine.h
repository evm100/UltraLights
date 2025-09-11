#pragma once
#include <stdint.h>
#include <stdbool.h>

void ul_white_engine_start(void);
void ul_white_engine_stop(void);

typedef struct cJSON cJSON;

// Parse and apply a JSON payload for white/set
void ul_white_apply_json(cJSON* root);

// Channels 0..3 (enabled by Kconfig flags). Returns false if channel not enabled.
bool ul_white_set_effect(int ch, const char* name);
bool ul_white_set_brightness(int ch, uint8_t bri);

// Status API
typedef struct {
    bool enabled;
    char effect[24];
    uint8_t brightness;   // 0..255
    int pwm_hz;
    int gpio;
} ul_white_ch_status_t;

int ul_white_get_channel_count(void); // up to 4
bool ul_white_get_status(int ch, ul_white_ch_status_t* out);

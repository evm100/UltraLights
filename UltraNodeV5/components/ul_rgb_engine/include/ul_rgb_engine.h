#pragma once
#include <stdint.h>
#include <stdbool.h>

typedef struct cJSON cJSON;

void ul_rgb_engine_start(void);
void ul_rgb_engine_stop(void);

void ul_rgb_apply_json(cJSON* root);

bool ul_rgb_set_effect(int strip, const char* name);
bool ul_rgb_set_brightness(int strip, uint8_t bri);
void ul_rgb_set_solid_rgb(int strip, uint8_t r, uint8_t g, uint8_t b);
void ul_rgb_get_solid_rgb(int strip, uint8_t* r, uint8_t* g, uint8_t* b);

// Status API
typedef struct {
    bool enabled;
    char effect[24];
    uint8_t brightness;
    int pwm_hz;
    struct {
        int gpio;
        int ledc_ch;
        int ledc_mode; // 0 = low speed, 1 = high speed
    } channel[3];
    uint8_t color[3];
} ul_rgb_strip_status_t;

int ul_rgb_get_strip_count(void);
bool ul_rgb_get_status(int strip, ul_rgb_strip_status_t* out);

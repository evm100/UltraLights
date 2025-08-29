#pragma once
#include <stdint.h>
#include <stdbool.h>

void ul_ws_engine_start(void);

// Control API
bool ul_ws_set_effect(int strip, const char* name);     // returns true if found
void ul_ws_set_solid_rgb(int strip, uint8_t r, uint8_t g, uint8_t b);
void ul_ws_set_brightness(int strip, uint8_t bri);      // 0..255
void ul_ws_power(int strip, bool on);

// Utility: convert "#RRGGBB" string to RGB components
bool ul_ws_hex_to_rgb(const char* hex, uint8_t* r, uint8_t* g, uint8_t* b);

typedef struct {
    uint8_t r, g, b;
    float freq;
    float velocity;
} ul_ws_wave_cfg_t;

void ul_ws_triple_wave_set(int strip, const ul_ws_wave_cfg_t waves[3]);
const ul_ws_wave_cfg_t* ul_ws_triple_wave_get(int strip);

// Status API
typedef struct {
    bool enabled;
    bool power;
    char effect[24];
    uint8_t brightness;
    int pixels;
    int gpio;
    int fps;
    uint8_t color[3]; // for solid
} ul_ws_strip_status_t;

int ul_ws_get_strip_count(void);
bool ul_ws_get_status(int strip, ul_ws_strip_status_t* out);

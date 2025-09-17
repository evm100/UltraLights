#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include "ul_ws_engine.h"
#include "cJSON.h"

static int s_wavelength[2] = {32, 32};

static void hue_to_rgb(uint8_t h, uint8_t* r, uint8_t* g, uint8_t* b) {
    h = 255 - h;
    if (h < 85) {
        *r = 255 - h * 3;
        *g = 0;
        *b = h * 3;
    } else if (h < 170) {
        h -= 85;
        *r = 0;
        *g = h * 3;
        *b = 255 - h * 3;
    } else {
        h -= 170;
        *r = h * 3;
        *g = 255 - h * 3;
        *b = 0;
    }
}

void rainbow_init(void) { }

void rainbow_apply_params(int strip, const cJSON* params) {
    if (strip < 0 || strip > 1) return;
    if (!params || !cJSON_IsArray(params) || cJSON_GetArraySize(params) < 1) return;
    int w = cJSON_GetArrayItem(params, 0)->valueint;
    if (w <= 0) w = 1;
    s_wavelength[strip] = w;
}

void rainbow_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    int strip = ul_ws_effect_current_strip();
    int w = s_wavelength[strip];
    if (w <= 0) w = 1;
    for (int i = 0; i < pixels; ++i) {
        int pos = (i + frame_idx) % w;
        uint8_t hue = (uint8_t)((pos * 255) / w);
        uint8_t r, g, b;
        hue_to_rgb(hue, &r, &g, &b);
        frame_rgb[3*i+0] = r;
        frame_rgb[3*i+1] = g;
        frame_rgb[3*i+2] = b;
    }
}

#endif


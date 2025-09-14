#include "effect.h"
#include "ul_ws_engine.h"

static void hsv_to_rgb(uint8_t h, uint8_t *r, uint8_t *g, uint8_t *b) {
    uint8_t region = h / 43;
    uint8_t remainder = (h - region * 43) * 6;

    uint8_t v = 255;
    uint8_t p = 0;
    uint8_t q = (uint8_t)(255 - ((uint16_t)remainder * 255 >> 8));
    uint8_t t = (uint8_t)((uint16_t)remainder * 255 >> 8);

    switch (region) {
        case 0: *r = v; *g = t; *b = p; break;
        case 1: *r = q; *g = v; *b = p; break;
        case 2: *r = p; *g = v; *b = t; break;
        case 3: *r = p; *g = q; *b = v; break;
        case 4: *r = t; *g = p; *b = v; break;
        default: *r = v; *g = p; *b = q; break;
    }
}

void modern_rainbow_init(void) { }

void modern_rainbow_render(uint8_t *frame_rgb, int pixels, int frame_idx) {
    const int cycle = 80;
    for (int i = 0; i < pixels; ++i) {
        uint8_t hue = (uint8_t)((i * 256 / cycle + frame_idx) & 0xFF);
        uint8_t r, g, b;
        hsv_to_rgb(hue, &r, &g, &b);
        frame_rgb[3 * i + 0] = r;
        frame_rgb[3 * i + 1] = g;
        frame_rgb[3 * i + 2] = b;
    }
}

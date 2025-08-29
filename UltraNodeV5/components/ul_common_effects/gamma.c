#include "ul_common_effects.h"
#include <math.h>
#include <stdbool.h>

static uint8_t s_gamma_tbl[256];
static bool s_gamma_init = false;

static void init_gamma_table(void) {
    for (int i = 0; i < 256; ++i) {
        float f = (float)i / 255.0f;
        float g = powf(f, 2.2f);
        int out = (int)(g * 255.0f + 0.5f);
        if (out < 0) out = 0;
        if (out > 255) out = 255;
        s_gamma_tbl[i] = (uint8_t)out;
    }
    s_gamma_init = true;
}

uint8_t ul_gamma8(uint8_t x) {
    if (!s_gamma_init) init_gamma_table();
    return s_gamma_tbl[x];
}

#include "ul_common_effects.h"
#include <math.h>
uint8_t ul_gamma8(uint8_t x) {
    float f = (float)x / 255.0f;
    float g = powf(f, 2.2f);
    int out = (int)(g * 255.0f + 0.5f);
    if (out < 0) out = 0;
    if (out > 255) out = 255;
    return (uint8_t)out;
}

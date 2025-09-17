#include "sdkconfig.h"

#if CONFIG_UL_WHT0_ENABLED || CONFIG_UL_WHT1_ENABLED || CONFIG_UL_WHT2_ENABLED || CONFIG_UL_WHT3_ENABLED

#include "effect.h"

void white_solid_init(void) {
    // no initialization needed
}

uint8_t white_solid_render(int frame_idx) {
    (void)frame_idx;
    return 255;
}

#endif


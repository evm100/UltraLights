#include "sdkconfig.h"

#if CONFIG_UL_WHT0_ENABLED || CONFIG_UL_WHT1_ENABLED || CONFIG_UL_WHT2_ENABLED || CONFIG_UL_WHT3_ENABLED

#include "effect.h"
#include <stdint.h>

#define WHITE_SWELL_STEP_INTERVAL_US 10000

static uint8_t compute_brightness_for_frame(int frame_idx) {
    if (frame_idx <= 0) {
        return 0;
    }

    int refresh_hz = CONFIG_UL_WHITE_SMOOTH_HZ;
    if (refresh_hz <= 0) {
        return 255;
    }

    int64_t elapsed_us = ((int64_t)frame_idx * 1000000LL) / refresh_hz;
    int64_t steps = elapsed_us / WHITE_SWELL_STEP_INTERVAL_US;
    if (steps < 0) {
        steps = 0;
    }
    if (steps > 255) {
        steps = 255;
    }
    return (uint8_t)steps;
}

void white_swell_init(void) {
    // No per-channel state is required; the frame index drives the swell.
}

uint8_t white_swell_render(int frame_idx) {
    return compute_brightness_for_frame(frame_idx);
}

#endif


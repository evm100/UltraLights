#include "sdkconfig.h"

#if CONFIG_UL_WHT0_ENABLED || CONFIG_UL_WHT1_ENABLED || CONFIG_UL_WHT2_ENABLED || CONFIG_UL_WHT3_ENABLED

#include "effect.h"
#include <stdint.h>

#define WHITE_SWELL_DURATION_MS 3000
// Ensure the swell walks every possible brightness value (0-255). With fewer
// frames than this we would be forced to skip steps, which looks abrupt.
#define WHITE_SWELL_MIN_FRAMES 256

static int compute_total_frames(void) {
    int frames = (WHITE_SWELL_DURATION_MS * CONFIG_UL_WHITE_SMOOTH_HZ) / 1000;
    if (frames < WHITE_SWELL_MIN_FRAMES) {
        frames = WHITE_SWELL_MIN_FRAMES;
    }
    return frames;
}

void white_swell_init(void) {
    // No per-channel state is required; the frame index drives the swell.
}

uint8_t white_swell_render(int frame_idx) {
    int frames = compute_total_frames();
    if (frame_idx <= 0) {
        return 0;
    }
    if (frame_idx >= frames - 1) {
        return 255;
    } 

    int value = (int)((((int64_t)frame_idx) * 255) / (frames - 1));
    if (value < 0) {
        value = 0;
    }
    if (value > 255) {
        value = 255;
    }
    return (uint8_t)value;
}

#endif


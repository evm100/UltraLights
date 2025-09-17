#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
#include <math.h>
void breathe_init(void) { (void)0; }
void breathe_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
float phase = (frame_idx % 120) / 120.0f; // 2s at 60 FPS
float a = 0.5f * (1.0f - cosf(phase * 2.0f * (float)M_PI));
uint8_t v = (uint8_t)(a*255);
for(int i=0;i<pixels;i++){frame_rgb[3*i]=v;frame_rgb[3*i+1]=v;frame_rgb[3*i+2]=v;}

}

#endif

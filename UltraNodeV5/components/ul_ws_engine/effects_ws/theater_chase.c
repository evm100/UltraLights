#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
void theater_chase_init(void) { (void)0; }
void theater_chase_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
for(int i=0;i<pixels;i++){uint8_t on = ((i+frame_idx)%3)==0; frame_rgb[3*i]=on?255:0; frame_rgb[3*i+1]=on?255:0; frame_rgb[3*i+2]=on?255:0;}

}

#endif

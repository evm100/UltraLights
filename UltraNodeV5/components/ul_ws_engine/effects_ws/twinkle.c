#include "sdkconfig.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

#include "effect.h"
void twinkle_init(void) { (void)0; }
void twinkle_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
for(int i=0;i<pixels;i++){uint8_t v = ((i*31+frame_idx*13)%255); frame_rgb[3*i]=v; frame_rgb[3*i+1]=0; frame_rgb[3*i+2]=v;}

}

#endif

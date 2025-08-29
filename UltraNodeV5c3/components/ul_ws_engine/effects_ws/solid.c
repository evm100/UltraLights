#include "effect.h"
void solid_init(void) { (void)0; }
void solid_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    for(int i=0;i<pixels;i++){frame_rgb[3*i+0]=255;frame_rgb[3*i+1]=0;frame_rgb[3*i+2]=0;}
}

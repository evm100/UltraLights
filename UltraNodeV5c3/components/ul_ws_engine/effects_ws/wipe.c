#include "effect.h"
void wipe_init(void) { (void)0; }
void wipe_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
int pos = frame_idx % (pixels+10);
for(int i=0;i<pixels;i++){uint8_t on = i<pos; frame_rgb[3*i]=on?255:0; frame_rgb[3*i+1]=on?255:0; frame_rgb[3*i+2]=on?255:0;}

}

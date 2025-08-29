#include "effect.h"
void rainbow_init(void) { (void)0; }
void rainbow_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
for(int i=0;i<pixels;i++){int k=(i+frame_idx)%pixels;frame_rgb[3*i]=(k*5)%256;frame_rgb[3*i+1]=(k*3)%256;frame_rgb[3*i+2]=(k*7)%256;}

}

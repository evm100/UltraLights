#include "effect.h"
void gradient_scroll_init(void) { (void)0; }
void gradient_scroll_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    (void)frame_idx;
    
for(int i=0;i<pixels;i++){int k=(i+frame_idx)%pixels;frame_rgb[3*i]=(k*2)%256;frame_rgb[3*i+1]=(255-(k*2)%256);frame_rgb[3*i+2]=(k*5)%256;}

}

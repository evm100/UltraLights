#include "effect.h"
#include <math.h>
uint8_t ul_wfx_breathe_render_w_render(int frame_idx) {
    float t = (frame_idx % 200) / 200.0f;
    float v = 0.5f*(1.0f - cosf(2*3.14159f*t));
    if (v<0) v=0;
    if (v>1) v=1;
    return (uint8_t)(v*255.0f + 0.5f);
}

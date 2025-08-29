#include "effect.h"
#include <math.h>
uint8_t motion_swell_render(int frame_idx) {
    float t = (frame_idx % 200) / 200.0f;
    float v = t<0.2f? (t/0.2f): (1.0f - (t-0.2f)/0.8f*0.3f);
    if (v<0) v=0;
    if (v>1) v=1;
    return (uint8_t)(v*255.0f + 0.5f);
}

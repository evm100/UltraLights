#include "ul_common_effects.h"
void ul_apply_transition(uint8_t* dst, const uint8_t* a, const uint8_t* b, int count, float alpha) {
    for (int i=0;i<count;i++) {
        int v = (int)(a[i]*(1.0f-alpha) + b[i]*alpha + 0.5f);
        if (v<0) v=0;
	if (v>255) v=255;
        dst[i]=(uint8_t)v;
    }
}

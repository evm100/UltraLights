#include "ul_common_effects.h"
#include <math.h>
float ul_ease_in_out(float t) { return 0.5f*(1.0f - cosf((float)M_PI*t)); }

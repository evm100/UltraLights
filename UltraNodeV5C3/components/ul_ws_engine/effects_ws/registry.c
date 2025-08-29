#include "effect.h"

void solid_init(void);        void solid_render(uint8_t*,int,int);
void breathe_init(void);      void breathe_render(uint8_t*,int,int);
void rainbow_init(void);      void rainbow_render(uint8_t*,int,int);
void twinkle_init(void);      void twinkle_render(uint8_t*,int,int);
void theater_chase_init(void);void theater_chase_render(uint8_t*,int,int);
void wipe_init(void);         void wipe_render(uint8_t*,int,int);
void noise_init(void);        void noise_render(uint8_t*,int,int);
void gradient_scroll_init(void);void gradient_scroll_render(uint8_t*,int,int);
void triple_wave_init(void);  void triple_wave_render(uint8_t*,int,int);

static const ws_effect_t effects[] = {
    {"solid", solid_init, solid_render},
    {"breathe", breathe_init, breathe_render},
    {"rainbow", rainbow_init, rainbow_render},
    {"twinkle", twinkle_init, twinkle_render},
    {"theater_chase", theater_chase_init, theater_chase_render},
    {"wipe", wipe_init, wipe_render},
    {"gradient_scroll", gradient_scroll_init, gradient_scroll_render},
    {"triple_wave", triple_wave_init, triple_wave_render},
};

const ws_effect_t* ul_ws_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}

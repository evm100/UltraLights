#include <stddef.h>
#include "effect.h"

void solid_init(void);        void solid_render(uint8_t*,int,int);        void solid_apply_params(int,const cJSON*);
void breathe_init(void);      void breathe_render(uint8_t*,int,int);
void rainbow_init(void);      void rainbow_render(uint8_t*,int,int);      void rainbow_apply_params(int,const cJSON*);
void modern_rainbow_init(void); void modern_rainbow_render(uint8_t*,int,int);
void twinkle_init(void);      void twinkle_render(uint8_t*,int,int);
void theater_chase_init(void);void theater_chase_render(uint8_t*,int,int);
void wipe_init(void);         void wipe_render(uint8_t*,int,int);
void noise_init(void);        void noise_render(uint8_t*,int,int);
void gradient_scroll_init(void);void gradient_scroll_render(uint8_t*,int,int);
void triple_wave_init(void);  void triple_wave_render(uint8_t*,int,int);   void triple_wave_apply_params(int,const cJSON*);
void flash_init(void);        void flash_render(uint8_t*,int,int);        void flash_apply_params(int,const cJSON*);
void spacewaves_init(void);   void spacewaves_render(uint8_t*,int,int);   void spacewaves_apply_params(int,const cJSON*);
void fire_init(void);         void fire_render(uint8_t*,int,int);         void fire_apply_params(int,const cJSON*);

static const ws_effect_t effects[] = {
    {"solid", solid_init, solid_render, solid_apply_params},
    {"breathe", breathe_init, breathe_render, NULL},
    {"rainbow", rainbow_init, rainbow_render, rainbow_apply_params},
    {"modern_rainbow", modern_rainbow_init, modern_rainbow_render, NULL},
    {"twinkle", twinkle_init, twinkle_render, NULL},
    {"theater_chase", theater_chase_init, theater_chase_render, NULL},
    {"wipe", wipe_init, wipe_render, NULL},
    {"gradient_scroll", gradient_scroll_init, gradient_scroll_render, NULL},
    {"triple_wave", triple_wave_init, triple_wave_render, triple_wave_apply_params},
    {"flash", flash_init, flash_render, flash_apply_params},
    {"spacewaves", spacewaves_init, spacewaves_render, spacewaves_apply_params},
    {"fire", fire_init, fire_render, fire_apply_params},
};

const ws_effect_t* ul_ws_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}

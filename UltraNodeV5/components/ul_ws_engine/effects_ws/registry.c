#include "sdkconfig.h"
#include <stddef.h>
#include "effect.h"

#if CONFIG_UL_WS0_ENABLED || CONFIG_UL_WS1_ENABLED

void solid_init(void);        void solid_render(uint8_t*,int,int);        void solid_apply_params(int,const cJSON*);
void color_swell_init(void);  void color_swell_render(uint8_t*,int,int);  void color_swell_apply_params(int,const cJSON*);
void rainbow_init(void);      void rainbow_render(uint8_t*,int,int);      void rainbow_apply_params(int,const cJSON*);
void modern_rainbow_init(void); void modern_rainbow_render(uint8_t*,int,int);
void noise_init(void);        void noise_render(uint8_t*,int,int);
void triple_wave_init(void);  void triple_wave_render(uint8_t*,int,int);   void triple_wave_apply_params(int,const cJSON*);
void flash_init(void);        void flash_render(uint8_t*,int,int);        void flash_apply_params(int,const cJSON*);
void spacewaves_init(void);   void spacewaves_render(uint8_t*,int,int);   void spacewaves_apply_params(int,const cJSON*);
#if CONFIG_UL_HAS_PSRAM
void fire_init(void);         void fire_render(uint8_t*,int,int);         void fire_apply_params(int,const cJSON*);
void black_ice_init(void);    void black_ice_render(uint8_t*,int,int);    void black_ice_apply_params(int,const cJSON*);
#endif

static const ws_effect_t effects[] = {
    {"solid", WS_EFFECT_TIER_STANDARD, solid_init, solid_render, solid_apply_params},
    {"color_swell", WS_EFFECT_TIER_STANDARD, color_swell_init, color_swell_render, color_swell_apply_params},
    {"rainbow", WS_EFFECT_TIER_STANDARD, rainbow_init, rainbow_render, rainbow_apply_params},
    {"modern_rainbow", WS_EFFECT_TIER_STANDARD, modern_rainbow_init, modern_rainbow_render, NULL},
    {"triple_wave", WS_EFFECT_TIER_STANDARD, triple_wave_init, triple_wave_render, triple_wave_apply_params},
    {"flash", WS_EFFECT_TIER_STANDARD, flash_init, flash_render, flash_apply_params},
    {"spacewaves", WS_EFFECT_TIER_STANDARD, spacewaves_init, spacewaves_render, spacewaves_apply_params},
#if CONFIG_UL_HAS_PSRAM
    {"fire", WS_EFFECT_TIER_PSRAM, fire_init, fire_render, fire_apply_params},
    {"black_ice", WS_EFFECT_TIER_PSRAM, black_ice_init, black_ice_render, black_ice_apply_params},
#endif
};

const ws_effect_t* ul_ws_get_effects(int* count) {
    if (count) *count = sizeof(effects)/sizeof(effects[0]);
    return effects;
}

#else

const ws_effect_t* ul_ws_get_effects(int* count) {
    if (count) *count = 0;
    return NULL;
}

#endif

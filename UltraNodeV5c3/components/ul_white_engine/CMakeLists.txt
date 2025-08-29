idf_component_register(SRCS "ul_white_engine.c" "effects_white/registry.c"
                            "effects_white/graceful_on.c" "effects_white/graceful_off.c" "effects_white/breathe.c"
                            "effects_white/motion_swell.c" "effects_white/day_night_curve.c" "effects_white/blink.c"
                       INCLUDE_DIRS "include" "effects_white"
                       REQUIRES json driver esp_timer ul_common_effects ul_task)

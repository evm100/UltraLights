idf_component_register(SRCS "ul_ws_engine.c" "effects_ws/registry.c"
                            "effects_ws/solid.c" "effects_ws/breathe.c" "effects_ws/rainbow.c"
                            "effects_ws/twinkle.c" "effects_ws/theater_chase.c" "effects_ws/wipe.c"
                            "effects_ws/gradient_scroll.c" "effects_ws/triple_wave.c" "effects_ws/flash.c" "effects_ws/spacewaves.c"
                       INCLUDE_DIRS "include" "effects_ws"
                       REQUIRES json led_strip driver esp_timer ul_common_effects ul_task
                       PRIV_REQUIRES ul_core)

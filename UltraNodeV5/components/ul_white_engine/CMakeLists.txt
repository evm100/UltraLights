idf_component_register(SRCS "ul_white_engine.c" "effects_white/registry.c" "effects_white/solid.c" "effects_white/breathe.c"
                       INCLUDE_DIRS "include" "effects_white"
                       REQUIRES json driver esp_timer ul_common_effects ul_task)

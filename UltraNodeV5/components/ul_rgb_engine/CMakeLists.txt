idf_component_register(SRCS "ul_rgb_engine.c" "effects_rgb/registry.c" "effects_rgb/solid.c" "effects_rgb/color_swell.c"
                       INCLUDE_DIRS "include" "effects_rgb"
                       REQUIRES json driver ul_common_effects ul_task)

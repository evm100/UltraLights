set(srcs)
if(CONFIG_UL_PWM_ENABLE)
    list(APPEND srcs "src/ul_pwm.c" "src/chip/ul_pwm_fade_${IDF_TARGET}.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES ul_core ul_board esp_driver_ledc esp_timer log
    WHOLE_ARCHIVE
)

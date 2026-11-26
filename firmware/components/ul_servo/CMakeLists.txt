set(srcs)
if(CONFIG_UL_SERVO_ENABLE)
    list(APPEND srcs "src/ul_servo.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_REQUIRES ul_core ul_board esp_driver_ledc esp_timer log
    WHOLE_ARCHIVE
)

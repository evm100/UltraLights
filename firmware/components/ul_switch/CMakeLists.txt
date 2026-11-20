set(srcs)
if(CONFIG_UL_SWITCH_ENABLE)
    list(APPEND srcs "src/ul_switch.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_REQUIRES ul_core ul_board esp_driver_gpio log
    WHOLE_ARCHIVE
)

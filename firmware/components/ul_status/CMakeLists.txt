set(srcs)
if(CONFIG_UL_STATUS_ENABLE)
    list(APPEND srcs "src/ul_status.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_REQUIRES ul_core ul_board esp_driver_gpio esp_timer
    WHOLE_ARCHIVE
)

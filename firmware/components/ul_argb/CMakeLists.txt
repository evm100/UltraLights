set(srcs)
if(CONFIG_UL_ARGB_ENABLE)
    list(APPEND srcs "src/ul_argb.c" "src/chip/ul_argb_chip_${IDF_TARGET}.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES ul_core ul_board esp_driver_rmt esp_timer log
    WHOLE_ARCHIVE
)

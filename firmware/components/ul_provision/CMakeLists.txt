set(srcs)
if(CONFIG_UL_PROVISION_ENABLE)
    list(APPEND srcs "src/ul_provision.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES ul_core ul_board ul_contract ul_wifi esp_http_server esp_wifi esp_driver_gpio
                  esp_timer esp_app_format lwip log
    WHOLE_ARCHIVE
)

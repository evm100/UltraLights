set(srcs)
if(CONFIG_UL_WIFI_ENABLE)
    list(APPEND srcs "src/ul_wifi.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES esp_netif
    PRIV_REQUIRES ul_core ul_board ul_contract esp_wifi esp_timer nvs_flash lwip log
    WHOLE_ARCHIVE
)

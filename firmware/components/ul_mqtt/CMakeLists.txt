set(srcs)
if(CONFIG_UL_MQTT_ENABLE)
    list(APPEND srcs "src/ul_mqtt.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES ul_contract
    PRIV_REQUIRES ul_core ul_board ul_identity ul_health ul_wifi mbedtls esp_timer esp_app_format log
    WHOLE_ARCHIVE
)

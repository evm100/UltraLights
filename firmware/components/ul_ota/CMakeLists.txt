set(srcs)
if(CONFIG_UL_OTA_ENABLE)
    list(APPEND srcs "src/ul_ota.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_REQUIRES ul_core ul_identity ul_mqtt esp_https_ota esp_http_client app_update esp_partition
                  mbedtls log
    WHOLE_ARCHIVE
)

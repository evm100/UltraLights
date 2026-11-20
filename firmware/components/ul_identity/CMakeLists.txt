set(srcs "src/ul_identity_crypto.c")
if(CONFIG_UL_IDENTITY_ENABLE)
    list(APPEND srcs "src/ul_identity.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    PRIV_REQUIRES ul_core ul_board ul_contract ul_wifi mbedtls esp_http_client esp_timer nvs_flash
                  esp_app_format log
    WHOLE_ARCHIVE
)

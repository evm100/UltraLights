idf_component_register(SRCS "ul_ota.c"
                       INCLUDE_DIRS "include"
                       REQUIRES esp_https_ota esp_http_client mbedtls nvs_flash ul_core ul_mqtt)

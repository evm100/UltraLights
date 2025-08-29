idf_component_register(SRCS "main.c"
                       INCLUDE_DIRS "."
                       REQUIRES esp_wifi esp_event esp_netif mqtt nvs_flash driver cjson led_strip)

idf_component_register(SRCS "ul_core.c"
                       INCLUDE_DIRS "include"
                       REQUIRES nvs_flash esp_wifi esp_event esp_netif esp_timer lwip)

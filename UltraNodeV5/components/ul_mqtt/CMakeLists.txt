idf_component_register(SRCS "ul_mqtt.c"
                       INCLUDE_DIRS "include"
                       REQUIRES mqtt json nvs_flash esp_event esp_timer ul_core ul_ws_engine ul_white_engine ul_rgb_engine ul_ota ul_state)

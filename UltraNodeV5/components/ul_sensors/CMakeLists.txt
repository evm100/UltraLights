idf_component_register(SRCS "ul_sensors.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver esp_timer ul_mqtt ul_task ul_white_engine)

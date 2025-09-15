idf_component_register(SRCS "ul_pir.c"
                       INCLUDE_DIRS "include"
                       REQUIRES ul_mqtt ul_task driver esp_timer)

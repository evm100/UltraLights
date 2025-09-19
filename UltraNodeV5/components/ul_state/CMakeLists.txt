idf_component_register(SRCS "ul_state.c"
                       INCLUDE_DIRS "include"
                       REQUIRES nvs_flash esp_timer esp_event freertos ul_task)

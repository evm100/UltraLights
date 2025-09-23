idf_component_register(SRCS "ul_health.c"
                       INCLUDE_DIRS "include"
                       REQUIRES ul_core esp_timer esp_system
                       PRIV_REQUIRES ul_task)

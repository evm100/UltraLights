idf_component_register(
    SRCS "src/ul_health.c"
    INCLUDE_DIRS "include"
    REQUIRES ul_contract
    PRIV_REQUIRES ul_core ul_wifi esp_driver_tsens espcoredump app_update esp_timer log
    WHOLE_ARCHIVE
)

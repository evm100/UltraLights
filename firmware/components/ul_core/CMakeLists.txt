idf_component_register(
    SRCS "src/ul_core.c" "src/ul_core_logic.c"
    INCLUDE_DIRS "include"
    REQUIRES ul_contract esp_event
    PRIV_REQUIRES ul_board nvs_flash esp_timer esp_netif log
)

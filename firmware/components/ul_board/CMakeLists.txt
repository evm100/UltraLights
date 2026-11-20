idf_component_register(
    SRCS "src/ul_board.c" "src/ul_board_rules.c" "src/ul_chip_rules.c"
         "src/chip/ul_board_chip_${IDF_TARGET}.c"
    INCLUDE_DIRS "include"
    REQUIRES ul_contract esp_partition
    PRIV_REQUIRES log
)

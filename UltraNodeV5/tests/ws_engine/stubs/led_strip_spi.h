#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint32_t clk_src;
    int spi_bus;
    struct {
        bool with_dma;
    } flags;
} led_strip_spi_config_t;

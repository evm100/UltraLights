#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "led_strip_types.h"
#include "led_strip_spi.h"

typedef struct led_strip_stub {
    int id;
    bool cleared;
    bool deleted;
    int set_pixel_calls;
    int refresh_calls;
} led_strip_stub_t;

typedef led_strip_stub_t* led_strip_handle_t;

typedef struct {
    int strip_gpio_num;
    int max_leds;
    led_model_t led_model;
    struct {
        bool invert_out;
    } flags;
} led_strip_config_t;

esp_err_t led_strip_new_spi_device(const led_strip_config_t* config,
                                   const led_strip_spi_config_t* spi_config,
                                   led_strip_handle_t* out_handle);
esp_err_t led_strip_clear(led_strip_handle_t handle);
esp_err_t led_strip_set_pixel(led_strip_handle_t handle, int index, uint32_t red, uint32_t green, uint32_t blue);
esp_err_t led_strip_refresh(led_strip_handle_t handle);
esp_err_t led_strip_del(led_strip_handle_t handle);

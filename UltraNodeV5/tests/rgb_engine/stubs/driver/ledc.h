#pragma once
#include "esp_err.h"
#include <stdint.h>

typedef enum {
    LEDC_LOW_SPEED_MODE = 0,
    LEDC_HIGH_SPEED_MODE = 1,
} ledc_mode_t;

typedef int ledc_channel_t;

typedef struct {
    ledc_mode_t speed_mode;
    int timer_num;
    int duty_resolution;
    int freq_hz;
    int clk_cfg;
} ledc_timer_config_t;

typedef struct {
    int gpio_num;
    ledc_mode_t speed_mode;
    ledc_channel_t channel;
    int intr_type;
    int timer_sel;
    int duty;
    int hpoint;
} ledc_channel_config_t;

#define LEDC_TIMER_0 0
#define LEDC_TIMER_12_BIT 12
#define LEDC_INTR_DISABLE 0
#define LEDC_AUTO_CLK 0

esp_err_t ledc_timer_config(const ledc_timer_config_t* config);
esp_err_t ledc_channel_config(const ledc_channel_config_t* config);
esp_err_t ledc_set_duty(ledc_mode_t mode, ledc_channel_t channel, int duty);
esp_err_t ledc_update_duty(ledc_mode_t mode, ledc_channel_t channel);

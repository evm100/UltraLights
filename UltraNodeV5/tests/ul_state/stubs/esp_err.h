#pragma once

#include <stdint.h>

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL 0x105
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_STATE 0x103

const char *esp_err_to_name(esp_err_t err);

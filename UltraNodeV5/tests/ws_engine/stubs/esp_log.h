#pragma once
#include <stdio.h>

#define ESP_LOGE(tag, fmt, ...) fprintf(stderr, "E (%s): " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) fprintf(stderr, "W (%s): " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) fprintf(stdout, "I (%s): " fmt "\n", tag, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) fprintf(stdout, "D (%s): " fmt "\n", tag, ##__VA_ARGS__)

#define ESP_ERROR_CHECK(x) do { \
    esp_err_t _err_rc = (x); \
    if (_err_rc != ESP_OK) { \
        fprintf(stderr, "ESP_ERROR_CHECK failed: %d\n", _err_rc); \
        abort(); \
    } \
} while (0)

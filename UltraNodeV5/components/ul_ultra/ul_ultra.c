#include "ul_ultra.h"
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ul_mqtt.h"
#include "esp_log.h"
#include "ul_task.h"
#include <stdio.h>
#include <stdbool.h>

static const char *TAG = "ul_ultra";
static TaskHandle_t s_ultra_task = NULL;
static int64_t s_last_publish_us = 0;

static void ultra_task(void *arg) {
    gpio_config_t trig = {
        .pin_bit_mask = 1ULL << CONFIG_UL_ULTRA_TRIG_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&trig);
    gpio_config_t echo = {
        .pin_bit_mask = 1ULL << CONFIG_UL_ULTRA_ECHO_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&echo);

    const int64_t min_interval_us = (int64_t)CONFIG_UL_ULTRA_EVENT_MIN_INTERVAL_S * 1000000LL;
    while (1) {
        int64_t now = esp_timer_get_time();
        if (now - s_last_publish_us < min_interval_us) {
            int64_t remain_ms = (min_interval_us - (now - s_last_publish_us)) / 1000;
            vTaskDelay(pdMS_TO_TICKS(remain_ms));
            continue;
        }

        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 0);
        esp_rom_delay_us(2);
        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 1);
        esp_rom_delay_us(10);
        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 0);

        bool timeout = false;
        int64_t wait_start = esp_timer_get_time();
        while (gpio_get_level(CONFIG_UL_ULTRA_ECHO_GPIO) == 0) {
            if (esp_timer_get_time() - wait_start > 25000) {
                timeout = true;
                break;
            }
        }

        float dist_cm = 0.0f;
        if (!timeout) {
            int64_t pulse_start = esp_timer_get_time();
            while (gpio_get_level(CONFIG_UL_ULTRA_ECHO_GPIO) == 1) {
                if (esp_timer_get_time() - pulse_start > 25000) {
                    timeout = true;
                    break;
                }
            }
            int64_t dur = esp_timer_get_time() - pulse_start;
            dist_cm = (float)dur / 58.0f; // HC-SR04 formula
        }

        if (!timeout && dist_cm > 0.0f && (dist_cm * 10.0f) < CONFIG_UL_ULTRA_DISTANCE_MM) {
            char msg[32];
            snprintf(msg, sizeof(msg), "MOTION_DETECTED:%.2f", dist_cm);
            ESP_LOGD(TAG, "Ultrasonic motion detected: %.2f cm", dist_cm);
            ul_mqtt_publish_motion("ultra", msg);
            s_last_publish_us = now;
            continue;
        }

        vTaskDelay(pdMS_TO_TICKS(CONFIG_UL_ULTRA_POLL_MS));
    }
}

void ul_ultra_start(void) {
    if (!s_ultra_task) {
        // Run ultrasonic measurements on the second core so the sensor's
        // busy-wait timing loops don't block work scheduled on core 0.
        ul_task_create(ultra_task, "ultra", 4096, NULL, 5, &s_ultra_task, 1);
    }
}

void ul_ultra_stop(void) {
    if (s_ultra_task) {
        vTaskDelete(s_ultra_task);
        s_ultra_task = NULL;
    }
}

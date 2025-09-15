#include "ul_pir.h"
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ul_mqtt.h"
#include "esp_log.h"
#include "ul_task.h"

static const char *TAG = "ul_pir";
static TaskHandle_t s_pir_task = NULL;
static int64_t s_last_publish_us = 0;

static void pir_task(void *arg) {
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << CONFIG_UL_PIR_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&cfg);

    const int64_t min_interval_us = (int64_t)CONFIG_UL_PIR_EVENT_MIN_INTERVAL_S * 1000000LL;
    while (1) {
        int64_t now = esp_timer_get_time();

        if (now - s_last_publish_us < min_interval_us) {
            int64_t remain_ms = (min_interval_us - (now - s_last_publish_us)) / 1000;
            vTaskDelay(pdMS_TO_TICKS(remain_ms));
            continue;
        }

        if (gpio_get_level(CONFIG_UL_PIR_GPIO)) {
            ESP_LOGD(TAG, "PIR motion detected");
            ul_mqtt_publish_motion("pir", "MOTION_DETECTED");
            s_last_publish_us = now;
            continue;
        }

        vTaskDelay(pdMS_TO_TICKS(CONFIG_UL_PIR_POLL_MS));
    }
}

void ul_pir_start(void) {
    if (!s_pir_task) {
        // Pin the PIR polling task to core 0 so time-sensitive LED work can
        // occupy the other CPU without contention.
        ul_task_create(pir_task, "pir", 2048, NULL, 5, &s_pir_task, 0);
    }
}

void ul_pir_stop(void) {
    if (s_pir_task) {
        vTaskDelete(s_pir_task);
        s_pir_task = NULL;
    }
}

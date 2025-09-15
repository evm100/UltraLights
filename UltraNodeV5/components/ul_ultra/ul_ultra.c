#include "ul_ultra.h"
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "driver/rmt.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "freertos/task.h"
#include "ul_mqtt.h"
#include "esp_log.h"
#include "ul_task.h"
#include <stdio.h>

static const char *TAG = "ul_ultra";
static TaskHandle_t s_ultra_task = NULL;
static int64_t s_last_publish_us = 0;
static const rmt_channel_t s_rmt_chan = RMT_CHANNEL_0;

static void ultra_task(void *arg) {
    gpio_config_t trig = {
        .pin_bit_mask = 1ULL << CONFIG_UL_ULTRA_TRIG_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&trig);

    rmt_config_t rx = {
        .rmt_mode = RMT_MODE_RX,
        .channel = s_rmt_chan,
        .clk_div = 80,
        .gpio_num = CONFIG_UL_ULTRA_ECHO_GPIO,
        .mem_block_num = 1,
        .rx_config = {
            .filter_en = true,
            .filter_ticks_thresh = 100,
            .idle_threshold = 25000,
        },
    };
    rmt_config(&rx);
    rmt_driver_install(s_rmt_chan, 1000, 0);
    RingbufHandle_t rb = NULL;
    rmt_get_ringbuf_handle(s_rmt_chan, &rb);

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

        rmt_rx_start(s_rmt_chan, true);
        size_t rx_size = 0;
        rmt_item32_t *item = (rmt_item32_t *)xRingbufferReceive(rb, &rx_size, pdMS_TO_TICKS(25));
        rmt_rx_stop(s_rmt_chan);

        int dist_mm = -1;
        if (item) {
            uint32_t dur = (item->level0 == 1) ? item->duration0 : item->duration1;
            vRingbufferReturnItem(rb, (void *)item);
            dist_mm = (int)(dur * 0.1715);
        }

        if (dist_mm > 0 && dist_mm < CONFIG_UL_ULTRA_DISTANCE_MM) {
            char msg[32];
            snprintf(msg, sizeof(msg), "MOTION_DETECTED:%d", dist_mm);
            ESP_LOGD(TAG, "Ultrasonic motion detected: %d mm", dist_mm);
            ul_mqtt_publish_motion("ultra", msg);
            s_last_publish_us = now;
            continue;
        }

        vTaskDelay(pdMS_TO_TICKS(CONFIG_UL_ULTRA_POLL_MS));
    }
}

void ul_ultra_start(void) {
    if (!s_ultra_task) {
        ul_task_create(ultra_task, "ultra", 4096, NULL, 5, &s_ultra_task, 0);
    }
}

void ul_ultra_stop(void) {
    if (s_ultra_task) {
        vTaskDelete(s_ultra_task);
        s_ultra_task = NULL;
        rmt_driver_uninstall(s_rmt_chan);
    }
}

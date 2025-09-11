#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"

#include "ul_core.h"
#include "ul_mqtt.h"
#include "ul_ota.h"
#include "ul_sensors.h"
#include "ul_ws_engine.h"
#include "ul_white_engine.h"

static const char *TAG = "app";

void app_main(void)
{
    ESP_LOGI(TAG, "UltraLights boot");

    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ul_core_wifi_start();
    ul_core_wait_for_ip(pdMS_TO_TICKS(10000));
    ul_core_sntp_start();

    ul_mqtt_start();

    ul_ws_engine_start();    // 60 FPS LED engine
    ul_white_engine_start(); // 200 Hz smoothing

    ul_sensors_start();

    ul_ota_start(); // periodic + MQTT trigger

    // Status heartbeat via MQTT
    while (true) {
        ul_mqtt_publish_status();
        vTaskDelay(pdMS_TO_TICKS(30 * 1000));
    }
}

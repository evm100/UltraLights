#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include <stdbool.h>
#include <stdint.h>

#include "ul_core.h"
#include "ul_mqtt.h"
#include "ul_task.h"
#include "ul_white_engine.h"
#include "ul_ws_engine.h"
#include "ul_rgb_engine.h"
#include "ul_task.h"
#if CONFIG_UL_PIR_ENABLED
#include "ul_pir.h"
#endif

static const char *TAG = "app";

static bool s_services_running = false;
static TaskHandle_t s_service_task = NULL;

static void service_manager_task(void *ctx) {
  uint32_t value;
  while (true) {
    if (xTaskNotifyWait(0, 0, &value, portMAX_DELAY) == pdTRUE) {
      bool connected = value;
      if (connected) {
        if (!s_services_running) {
          ul_mqtt_start();
          ul_ws_engine_start();    // 60 FPS LED engine
          ul_rgb_engine_start();   // RGB PWM engine
          ul_white_engine_start(); // 200 Hz smoothing
#if CONFIG_UL_PIR_ENABLED
          ul_pir_start();
#endif
          s_services_running = true;
        }
      } else {
        if (s_services_running) {
          ul_mqtt_stop();
          ul_ws_engine_stop();
          ul_rgb_engine_stop();
          ul_white_engine_stop();
#if CONFIG_UL_PIR_ENABLED
          ul_pir_stop();
#endif
          s_services_running = false;
        }
        ESP_LOGW(TAG, "Network disconnected");
      }
    }
  }
}

static void connectivity_cb(bool connected, void *ctx) {
  xTaskNotify(s_service_task, connected ? 1 : 0, eSetValueWithOverwrite);
}

void app_main(void) {
  ESP_LOGI(TAG, "UltraLights boot");

  ESP_ERROR_CHECK(nvs_flash_init());
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());

  ul_task_init();

  xTaskCreate(service_manager_task, "svc_mgr", 4096, NULL, 5,
              &s_service_task);
  ul_core_wifi_start();
  ul_core_register_connectivity_cb(connectivity_cb, NULL);
  bool connected = ul_core_wait_for_ip(portMAX_DELAY);
  if (!connected) {
    ESP_LOGE(TAG, "Failed to obtain IP address");
  }
  ul_core_sntp_start();
  ul_core_schedule_daily_reboot();

  // Status heartbeat via MQTT
  while (true) {
    if (ul_core_is_connected() && ul_mqtt_is_connected()) {
      ul_mqtt_publish_status();
    } else {
      ESP_LOGW(TAG, "Skipping status publish (disconnected)");
    }
    vTaskDelay(pdMS_TO_TICKS(30 * 1000));
  }
}

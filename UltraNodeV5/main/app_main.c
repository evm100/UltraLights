#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include <stdbool.h>

#include "ul_core.h"
#include "ul_mqtt.h"
#include "ul_ota.h"
#include "ul_sensors.h"
#include "ul_white_engine.h"
#include "ul_ws_engine.h"

static const char *TAG = "app";

static bool s_services_running = false;

static void connectivity_cb(bool connected, void *ctx) {
  int strips = ul_ws_get_strip_count();
  for (int i = 0; i < strips; ++i) {
    ul_ws_power(i, connected);
  }
  if (connected) {
    if (!s_services_running) {
      ul_mqtt_start();
      ul_ws_engine_start();    // 60 FPS LED engine
      ul_white_engine_start(); // 200 Hz smoothing
      ul_sensors_start();
      ul_ota_start(); // periodic + MQTT trigger
      s_services_running = true;
    }
  } else {
    if (s_services_running) {
      ul_mqtt_stop();
      s_services_running = false;
    }
    ESP_LOGW(TAG, "Network disconnected");
  }
}

void app_main(void) {
  ESP_LOGI(TAG, "UltraLights boot");

  ESP_ERROR_CHECK(nvs_flash_init());
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());

  ul_core_wifi_start();
  ul_core_register_connectivity_cb(connectivity_cb, NULL);
  bool connected = ul_core_wait_for_ip(portMAX_DELAY);
  connectivity_cb(connected, NULL);
  ul_core_sntp_start();

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

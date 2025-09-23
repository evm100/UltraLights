#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs_flash.h"
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#include "ul_core.h"
#include "ul_state.h"
#include "ul_mqtt.h"
#include "ul_task.h"
#include "ul_health.h"
#include "ul_white_engine.h"
#include "ul_ws_engine.h"
#include "ul_rgb_engine.h"
#if CONFIG_UL_PIR_ENABLED
#include "ul_pir.h"
#endif

static const char *TAG = "app";

static bool s_services_running = false;

typedef enum {
  SERVICE_MSG_CONNECTIVITY,
  SERVICE_MSG_RESTART_MQTT,
  SERVICE_MSG_RESTART_WIFI,
} service_msg_type_t;

typedef struct {
  service_msg_type_t type;
  bool connected;
} service_msg_t;

typedef struct {
  QueueHandle_t queue;
} service_context_t;

static QueueHandle_t s_service_queue = NULL;
static service_context_t s_service_ctx = {0};
static bool s_wifi_connected = false;

static void service_manager_task(void *ctx) {
  (void)ctx;
  service_msg_t msg;
  while (xQueueReceive(s_service_queue, &msg, portMAX_DELAY) == pdTRUE) {
    switch (msg.type) {
    case SERVICE_MSG_CONNECTIVITY: {
      bool connected = msg.connected;
      if (connected) {
        if (!s_wifi_connected) {
          s_wifi_connected = true;
          ESP_LOGI(TAG, "Network connected");
        }
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
        if (s_wifi_connected) {
          s_wifi_connected = false;
          ESP_LOGW(TAG, "Network disconnected");
        }
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
      }
      break;
    }
    case SERVICE_MSG_RESTART_MQTT:
      if (!s_services_running) {
        ESP_LOGW(TAG, "MQTT restart requested while services are stopped");
      } else {
        ESP_LOGW(TAG, "Health monitor requesting MQTT restart");
        ul_mqtt_restart();
      }
      break;
    case SERVICE_MSG_RESTART_WIFI:
      ESP_LOGW(TAG, "Health monitor requesting Wi-Fi restart");
      ul_core_wifi_restart();
      break;
    default:
      break;
    }
  }
}

static bool enqueue_service_message(QueueHandle_t queue, service_msg_type_t type,
                                    bool connected, TickType_t wait_ticks) {
  if (!queue)
    return false;
  service_msg_t msg = {
      .type = type,
      .connected = connected,
  };
  if (xQueueSend(queue, &msg, wait_ticks) != pdPASS) {
    ESP_LOGW(TAG, "Service queue full (msg=%d)", (int)type);
    return false;
  }
  return true;
}

static void request_wifi_recovery(void *ctx) {
  service_context_t *svc = (service_context_t *)ctx;
  QueueHandle_t queue = svc ? svc->queue : s_service_queue;
  if (!enqueue_service_message(queue, SERVICE_MSG_RESTART_WIFI, false,
                               pdMS_TO_TICKS(100))) {
    ESP_LOGW(TAG, "Failed to schedule Wi-Fi recovery");
  }
}

static void request_mqtt_recovery(void *ctx) {
  service_context_t *svc = (service_context_t *)ctx;
  QueueHandle_t queue = svc ? svc->queue : s_service_queue;
  if (!enqueue_service_message(queue, SERVICE_MSG_RESTART_MQTT, false,
                               pdMS_TO_TICKS(100))) {
    ESP_LOGW(TAG, "Failed to schedule MQTT recovery");
  }
}

static void connectivity_cb(bool connected, void *ctx) {
  (void)ctx;
  ul_health_notify_connectivity(connected);
  if (!enqueue_service_message(s_service_queue, SERVICE_MSG_CONNECTIVITY,
                               connected, pdMS_TO_TICKS(100))) {
    ESP_LOGW(TAG, "Dropping connectivity update (%d)", connected);
  }
}

void app_main(void) {
  ESP_LOGI(TAG, "UltraLights boot");

  ESP_ERROR_CHECK(nvs_flash_init());
  ul_state_init();
  ESP_ERROR_CHECK(esp_netif_init());
  ESP_ERROR_CHECK(esp_event_loop_create_default());

  ul_task_init();

  s_service_queue = xQueueCreate(16, sizeof(service_msg_t));
  if (!s_service_queue) {
    ESP_LOGE(TAG, "Failed to create service queue");
    abort();
  }
  s_service_ctx.queue = s_service_queue;

  if (xTaskCreate(service_manager_task, "svc_mgr", 4096, NULL, 5,
                  NULL) != pdPASS) {
    ESP_LOGE(TAG, "Failed to create service manager task");
    abort();
  }

  ul_health_config_t health_cfg = {
      .request_wifi_recovery = request_wifi_recovery,
      .request_mqtt_recovery = request_mqtt_recovery,
      .ctx = &s_service_ctx,
  };
  ul_health_start(&health_cfg);

  ul_core_wifi_start();
  ul_core_register_connectivity_cb(connectivity_cb, NULL);
  bool connected = ul_core_wait_for_ip(portMAX_DELAY);
  if (!connected) {
    ESP_LOGE(TAG, "Failed to obtain IP address");
  }
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

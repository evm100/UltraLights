#include "ul_core.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "sdkconfig.h"
#include "esp_sntp.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "ul_task.h"
#include "ul_wifi_credentials.h"
#include <string.h>
#include <time.h>
#include <sys/time.h>
#include <limits.h>

static const char *TAG = "ul_core";

static char s_node_id[32] = CONFIG_UL_NODE_ID;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT BIT1
#define WIFI_MAX_BACKOFF_MS 30000

#define SNTP_RETRY_INITIAL_DELAY_MS 5000
#define SNTP_RETRY_MAX_DELAY_MS 60000

static esp_timer_handle_t s_reconnect_timer;
static int s_backoff_ms = 1000;
static SemaphoreHandle_t s_wifi_restart_mutex;

static ul_core_time_sync_cb_t s_time_sync_cb = NULL;
static void *s_time_sync_ctx = NULL;

static TaskHandle_t s_sntp_task;
static esp_timer_handle_t s_sntp_retry_timer;
static uint32_t s_sntp_retry_delay_ms = SNTP_RETRY_INITIAL_DELAY_MS;
static uint32_t s_sntp_retry_attempts;
static uint64_t s_sntp_first_failure_us;
static uint64_t s_sntp_last_failure_us;
static portMUX_TYPE s_sntp_lock = portMUX_INITIALIZER_UNLOCKED;

static BaseType_t ul_core_start_sntp_task(void);
static void sntp_retry_timer_cb(void *arg);
static void schedule_sntp_retry(uint32_t delay_ms);

const char *ul_core_get_node_id(void) { return s_node_id; }

static ul_core_conn_cb_t s_conn_cb = NULL;
static void *s_conn_ctx = NULL;

void ul_core_register_connectivity_cb(ul_core_conn_cb_t cb, void *ctx) {
  s_conn_cb = cb;
  s_conn_ctx = ctx;
}

void ul_core_register_time_sync_cb(ul_core_time_sync_cb_t cb, void *ctx) {
  s_time_sync_cb = cb;
  s_time_sync_ctx = ctx;
}

static void wifi_reconnect_timer_cb(void *arg) {
  if (!s_wifi_event_group)
    return;
  xEventGroupClearBits(s_wifi_event_group, WIFI_FAIL_BIT);
  esp_err_t err = esp_wifi_connect();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_wifi_connect failed: %s", esp_err_to_name(err));
    esp_timer_start_once(s_reconnect_timer, s_backoff_ms * 1000);
  }
  s_backoff_ms = s_backoff_ms * 2;
  if (s_backoff_ms > WIFI_MAX_BACKOFF_MS)
    s_backoff_ms = WIFI_MAX_BACKOFF_MS;
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data) {
  if (!s_wifi_event_group) {
    ESP_LOGW(TAG, "Wi-Fi event received before event group init");
    return;
  }
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    s_backoff_ms = 1000;
    esp_wifi_connect();
  } else if (event_base == WIFI_EVENT &&
             event_id == WIFI_EVENT_STA_DISCONNECTED) {
    xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    if (s_conn_cb)
      s_conn_cb(false, s_conn_ctx);
    xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
    esp_timer_stop(s_reconnect_timer);
    esp_timer_start_once(s_reconnect_timer, s_backoff_ms * 1000);
  } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
    ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
    s_backoff_ms = 1000;
    xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    if (s_conn_cb)
      s_conn_cb(true, s_conn_ctx);
  }
}

void ul_core_wifi_start(void) {
  EventGroupHandle_t event_group = xEventGroupCreate();
  if (!event_group) {
    ESP_LOGE(TAG, "Failed to create Wi-Fi event group");
    if (s_reconnect_timer) {
      esp_timer_stop(s_reconnect_timer);
      esp_timer_delete(s_reconnect_timer);
      s_reconnect_timer = NULL;
    }
    if (s_wifi_restart_mutex) {
      vSemaphoreDelete(s_wifi_restart_mutex);
      s_wifi_restart_mutex = NULL;
    }
    return;
  }

  s_wifi_event_group = event_group;

  if (!s_wifi_restart_mutex) {
    s_wifi_restart_mutex = xSemaphoreCreateMutex();
    if (!s_wifi_restart_mutex) {
      ESP_LOGE(TAG, "Failed to allocate Wi-Fi restart mutex");
    }
  }

  const esp_timer_create_args_t reconnect_timer_args = {
      .callback = &wifi_reconnect_timer_cb,
      .name = "wifi_reconnect",
  };
  ESP_ERROR_CHECK(esp_timer_create(&reconnect_timer_args, &s_reconnect_timer));

  esp_netif_create_default_wifi_sta();
  wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&cfg));

  ul_wifi_credentials_t creds = {0};
  if (!ul_wifi_credentials_load(&creds)) {
    ESP_LOGE(TAG, "No stored Wi-Fi credentials; cannot start station");
    return;
  }

  wifi_config_t sta_cfg = {0};
  strlcpy((char *)sta_cfg.sta.ssid, creds.ssid, sizeof(sta_cfg.sta.ssid));
  strlcpy((char *)sta_cfg.sta.password, creds.password, sizeof(sta_cfg.sta.password));
  sta_cfg.sta.threshold.authmode =
      (creds.password[0] != '\0') ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
  sta_cfg.sta.ssid_len = strlen(creds.ssid);

  ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START,
                                             &wifi_event_handler, NULL));
  ESP_ERROR_CHECK(esp_event_handler_register(
      WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &wifi_event_handler, NULL));
  ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                             &wifi_event_handler, NULL));

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
  ESP_ERROR_CHECK(esp_wifi_start());
}

bool ul_core_wait_for_ip(TickType_t timeout) {
  if (!s_wifi_event_group)
    return false;
  TickType_t start = xTaskGetTickCount();
  TickType_t remaining = timeout;
  while (1) {
    EventBits_t bits = xEventGroupWaitBits(
        s_wifi_event_group, WIFI_CONNECTED_BIT | WIFI_FAIL_BIT, pdFALSE,
        pdFALSE, remaining);
    if (bits & WIFI_CONNECTED_BIT)
      return true;
    if (bits & WIFI_FAIL_BIT) {
      xEventGroupClearBits(s_wifi_event_group, WIFI_FAIL_BIT);
      TickType_t now = xTaskGetTickCount();
      if (now - start >= timeout)
        return false;
      remaining = timeout - (now - start);
      continue;
    }
    return false;
  }
}

bool ul_core_is_connected(void) {
  if (!s_wifi_event_group)
    return false;
  EventBits_t bits = xEventGroupGetBits(s_wifi_event_group);
  return (bits & WIFI_CONNECTED_BIT) != 0;
}

void ul_core_wifi_stop(void) {
  if (s_reconnect_timer) {
    esp_timer_stop(s_reconnect_timer);
    esp_timer_delete(s_reconnect_timer);
    s_reconnect_timer = NULL;
  }

  if (s_conn_cb) {
    s_conn_cb(false, s_conn_ctx);
  }

  ESP_ERROR_CHECK(esp_event_handler_unregister(WIFI_EVENT, WIFI_EVENT_STA_START,
                                               &wifi_event_handler));
  ESP_ERROR_CHECK(esp_event_handler_unregister(
      WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &wifi_event_handler));
  ESP_ERROR_CHECK(esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler));

  ESP_ERROR_CHECK(esp_wifi_stop());
  ESP_ERROR_CHECK(esp_wifi_deinit());

  if (s_wifi_event_group) {
    vEventGroupDelete(s_wifi_event_group);
    s_wifi_event_group = NULL;
  }
}

void ul_core_wifi_restart(void) {
  if (!s_wifi_restart_mutex) {
    s_wifi_restart_mutex = xSemaphoreCreateMutex();
    if (!s_wifi_restart_mutex) {
      ESP_LOGE(TAG, "Wi-Fi restart requested but mutex allocation failed");
      return;
    }
  }

  if (xSemaphoreTake(s_wifi_restart_mutex, pdMS_TO_TICKS(10000)) != pdTRUE) {
    ESP_LOGW(TAG, "Failed to obtain Wi-Fi restart mutex");
    return;
  }

  ESP_LOGW(TAG, "Restarting Wi-Fi stack");
  ul_core_wifi_stop();
  vTaskDelay(pdMS_TO_TICKS(200));
  ul_core_wifi_start();

  xSemaphoreGive(s_wifi_restart_mutex);
}

static void sntp_sync_task(void *arg) {
  const TickType_t interval =
      pdMS_TO_TICKS(CONFIG_UL_SNTP_SYNC_INTERVAL_S * 1000);
  while (1) {
    vTaskDelay(interval);
    while (!ul_core_is_connected()) {
      vTaskDelay(pdMS_TO_TICKS(1000));
    }
    esp_sntp_stop();
    esp_err_t err = esp_netif_sntp_start();
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "SNTP resync failed: %s", esp_err_to_name(err));
    }
  }
}

static void sntp_time_sync_notification_cb(struct timeval *tv) {
  if (s_time_sync_cb)
    s_time_sync_cb(s_time_sync_ctx);
}

void ul_core_sntp_start(void) {
  const char *tz = CONFIG_UL_TIMEZONE;
  if (tz[0] == '\0') {
    tz = "UTC";
  }
  setenv("TZ", tz, 1);
  tzset();

  esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
  esp_netif_sntp_init(&config);
  esp_sntp_set_time_sync_notification_cb(sntp_time_sync_notification_cb);
  ESP_ERROR_CHECK(esp_netif_sntp_start());

  // Wait until time is set (epoch > 1700000000 ~ 2023)
  time_t now = 0;
  struct tm timeinfo = {0};
  int retries = 0;
  const int max_retries = 20;
  while (retries++ < max_retries) {
    time(&now);
    localtime_r(&now, &timeinfo);
    if (now > 1700000000)
      break;
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
  ESP_LOGI(TAG, "Time sync: %ld", now);
  if (s_time_sync_cb)
    s_time_sync_cb(s_time_sync_ctx);
  if (!s_sntp_retry_timer) {
    const esp_timer_create_args_t retry_args = {
        .callback = &sntp_retry_timer_cb,
        .name = "sntp_retry",
    };
    esp_err_t timer_err = esp_timer_create(&retry_args, &s_sntp_retry_timer);
    if (timer_err != ESP_OK) {
      ESP_LOGE(TAG, "Failed to create SNTP retry timer: %s",
               esp_err_to_name(timer_err));
    }
  }

  portENTER_CRITICAL(&s_sntp_lock);
  s_sntp_retry_delay_ms = SNTP_RETRY_INITIAL_DELAY_MS;
  portEXIT_CRITICAL(&s_sntp_lock);

  if (ul_core_start_sntp_task() != pdPASS) {
    ESP_LOGW(TAG, "SNTP resync task creation deferred; retry scheduled");
  }
}

static BaseType_t ul_core_start_sntp_task(void) {
  TaskHandle_t existing;
  portENTER_CRITICAL(&s_sntp_lock);
  existing = s_sntp_task;
  portEXIT_CRITICAL(&s_sntp_lock);
  if (existing)
    return pdPASS;

  TaskHandle_t task_handle = NULL;
  BaseType_t rc = ul_task_create(sntp_sync_task, "sntp_sync", 2048, NULL,
                                 tskIDLE_PRIORITY, &task_handle, 0);
  if (rc == pdPASS) {
    portENTER_CRITICAL(&s_sntp_lock);
    s_sntp_task = task_handle;
    s_sntp_retry_attempts = 0;
    s_sntp_first_failure_us = 0;
    s_sntp_last_failure_us = 0;
    s_sntp_retry_delay_ms = SNTP_RETRY_INITIAL_DELAY_MS;
    portEXIT_CRITICAL(&s_sntp_lock);
    if (s_sntp_retry_timer && esp_timer_is_active(s_sntp_retry_timer)) {
      esp_timer_stop(s_sntp_retry_timer);
    }
    return rc;
  }

  uint64_t now_us = esp_timer_get_time();
  uint32_t attempt;
  uint32_t delay_ms;
  portENTER_CRITICAL(&s_sntp_lock);
  if (s_sntp_retry_attempts == 0)
    s_sntp_first_failure_us = now_us;
  if (s_sntp_retry_attempts < UINT32_MAX)
    s_sntp_retry_attempts++;
  attempt = s_sntp_retry_attempts;
  s_sntp_last_failure_us = now_us;
  delay_ms = s_sntp_retry_delay_ms;
  if (s_sntp_retry_delay_ms < SNTP_RETRY_MAX_DELAY_MS) {
    uint32_t next_delay = s_sntp_retry_delay_ms * 2;
    if (next_delay > SNTP_RETRY_MAX_DELAY_MS)
      next_delay = SNTP_RETRY_MAX_DELAY_MS;
    s_sntp_retry_delay_ms = next_delay;
  }
  portEXIT_CRITICAL(&s_sntp_lock);

  ESP_LOGE(TAG, "Failed to start SNTP resync task (attempt %u): %ld. Retrying in %u ms",
           (unsigned)attempt, (long)rc, (unsigned)delay_ms);
  schedule_sntp_retry(delay_ms);
  return rc;
}

static void sntp_retry_timer_cb(void *arg) {
  (void)arg;
  ul_core_start_sntp_task();
}

static void schedule_sntp_retry(uint32_t delay_ms) {
  if (!s_sntp_retry_timer) {
    ESP_LOGE(TAG, "SNTP retry timer unavailable; cannot reschedule");
    return;
  }
  if (esp_timer_is_active(s_sntp_retry_timer)) {
    esp_timer_stop(s_sntp_retry_timer);
  }
  esp_err_t err = esp_timer_start_once(s_sntp_retry_timer, delay_ms * 1000ULL);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to schedule SNTP retry in %u ms: %s", (unsigned)delay_ms,
             esp_err_to_name(err));
  }
}

bool ul_core_is_sntp_resync_active(void) {
  bool active;
  portENTER_CRITICAL(&s_sntp_lock);
  active = s_sntp_task != NULL;
  portEXIT_CRITICAL(&s_sntp_lock);
  return active;
}

uint32_t ul_core_get_sntp_retry_attempts(void) {
  uint32_t attempts;
  portENTER_CRITICAL(&s_sntp_lock);
  attempts = s_sntp_retry_attempts;
  portEXIT_CRITICAL(&s_sntp_lock);
  return attempts;
}

uint64_t ul_core_get_sntp_first_failure_us(void) {
  uint64_t first;
  portENTER_CRITICAL(&s_sntp_lock);
  first = s_sntp_first_failure_us;
  portEXIT_CRITICAL(&s_sntp_lock);
  return first;
}

uint64_t ul_core_get_sntp_last_failure_us(void) {
  uint64_t last;
  portENTER_CRITICAL(&s_sntp_lock);
  last = s_sntp_last_failure_us;
  portEXIT_CRITICAL(&s_sntp_lock);
  return last;
}

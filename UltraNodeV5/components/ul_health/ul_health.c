#include "ul_health.h"

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ul_core.h"
#include "ul_task.h"

#include <inttypes.h>
#include <limits.h>
#include <string.h>

#define UL_HEALTH_MONITOR_PERIOD_MS (60 * 1000)
#define UL_HEALTH_LOG_INTERVAL_US (15ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_WIFI_RECOVERY_DELAY_US (15ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_WIFI_RECOVERY_RETRY_US (10ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_WIFI_ESCALATE_US (6ULL * 60ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_WIFI_MAX_RECOVERIES 4
#define UL_HEALTH_MQTT_RECOVERY_DELAY_US (5ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_MQTT_RECOVERY_RETRY_US (5ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_MQTT_ESCALATE_US (2ULL * 60ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_MQTT_MAX_RECOVERIES 6
#define UL_HEALTH_HEAP_LOW_THRESHOLD (20 * 1024)
#define UL_HEALTH_HEAP_LOW_STRIKES 5
#define UL_HEALTH_TIME_SYNC_WARN_US (24ULL * 60ULL * 60ULL * 1000000ULL)
#define UL_HEALTH_TIME_SYNC_ERROR_US (7ULL * 24ULL * 60ULL * 60ULL * 1000000ULL)

static const char *TAG = "ul_health";

typedef struct {
  bool started;
  bool wifi_connected;
  bool mqtt_connected;
  bool time_sync_seen;
  uint32_t wifi_recovery_attempts;
  uint32_t mqtt_recovery_attempts;
  uint32_t heap_low_strikes;
  uint64_t wifi_last_change_us;
  uint64_t mqtt_last_change_us;
  uint64_t last_wifi_recovery_us;
  uint64_t last_mqtt_recovery_us;
  uint64_t last_metrics_log_us;
  uint64_t last_time_sync_us;
} ul_health_state_t;

static ul_health_state_t s_state;
static ul_health_config_t s_config;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static TaskHandle_t s_health_task;
static uint32_t s_last_sntp_retry_log_count;
static uint64_t s_last_sntp_retry_log_us;

static void health_task(void *arg);
static void health_take_snapshot(ul_health_state_t *state_out, ul_health_config_t *cfg_out);
static bool health_mark_wifi_recovery_attempt(uint64_t now_us, bool count_attempt,
                                              uint32_t *attempt_out);
static bool health_mark_mqtt_recovery_attempt(uint64_t now_us, uint32_t *attempt_out);
static uint32_t health_update_heap_low(bool low);
static void health_mark_metrics_logged(uint64_t now_us);
static void health_time_sync_cb(void *ctx);

void ul_health_start(const ul_health_config_t *config) {
  if (!config) {
    ESP_LOGE(TAG, "Health monitor requires configuration");
    return;
  }

  uint64_t now_us = esp_timer_get_time();
  bool already_started = false;
  bool wifi_now = ul_core_is_connected();

  portENTER_CRITICAL(&s_lock);
  if (s_state.started) {
    already_started = true;
  } else {
    memset(&s_state, 0, sizeof(s_state));
    s_config = *config;
    s_state.started = true;
    s_state.wifi_connected = wifi_now;
    s_state.mqtt_connected = false;
    s_state.wifi_last_change_us = now_us;
    s_state.mqtt_last_change_us = now_us;
    s_state.last_wifi_recovery_us = now_us;
    s_state.last_mqtt_recovery_us = now_us;
    s_state.last_metrics_log_us = now_us;
    s_state.last_time_sync_us = now_us;
  }
  portEXIT_CRITICAL(&s_lock);

  if (already_started) {
    ESP_LOGW(TAG, "Health monitor already started");
    return;
  }

  ESP_LOGI(TAG, "Health monitor started (wifi %s)", wifi_now ? "up" : "down");
  ul_core_register_time_sync_cb(health_time_sync_cb, NULL);

  if (ul_task_create(health_task, "ul_health", 4096, NULL, 4, &s_health_task, 0) != pdPASS) {
    ESP_LOGE(TAG, "Failed to start health task");
    portENTER_CRITICAL(&s_lock);
    memset(&s_state, 0, sizeof(s_state));
    memset(&s_config, 0, sizeof(s_config));
    portEXIT_CRITICAL(&s_lock);
  }
}

void ul_health_notify_connectivity(bool connected) {
  uint64_t now_us = esp_timer_get_time();
  portENTER_CRITICAL(&s_lock);
  if (!s_state.started) {
    portEXIT_CRITICAL(&s_lock);
    return;
  }
  s_state.wifi_connected = connected;
  s_state.wifi_last_change_us = now_us;
  if (connected) {
    s_state.wifi_recovery_attempts = 0;
    s_state.last_wifi_recovery_us = now_us;
  }
  portEXIT_CRITICAL(&s_lock);
}

void ul_health_notify_mqtt(bool connected) {
  uint64_t now_us = esp_timer_get_time();
  portENTER_CRITICAL(&s_lock);
  if (!s_state.started) {
    portEXIT_CRITICAL(&s_lock);
    return;
  }
  s_state.mqtt_connected = connected;
  s_state.mqtt_last_change_us = now_us;
  if (connected) {
    s_state.mqtt_recovery_attempts = 0;
    s_state.last_mqtt_recovery_us = now_us;
  }
  portEXIT_CRITICAL(&s_lock);
}

void ul_health_notify_time_sync(void) {
  uint64_t now_us = esp_timer_get_time();
  portENTER_CRITICAL(&s_lock);
  if (s_state.started) {
    s_state.time_sync_seen = true;
    s_state.last_time_sync_us = now_us;
  }
  portEXIT_CRITICAL(&s_lock);
}

static void health_time_sync_cb(void *ctx) {
  (void)ctx;
  ul_health_notify_time_sync();
}

static void health_take_snapshot(ul_health_state_t *state_out, ul_health_config_t *cfg_out) {
  portENTER_CRITICAL(&s_lock);
  *state_out = s_state;
  *cfg_out = s_config;
  portEXIT_CRITICAL(&s_lock);
}

static uint32_t health_update_heap_low(bool low) {
  uint32_t strikes;
  portENTER_CRITICAL(&s_lock);
  if (low) {
    if (s_state.heap_low_strikes < UINT32_MAX)
      s_state.heap_low_strikes++;
  } else {
    s_state.heap_low_strikes = 0;
  }
  strikes = s_state.heap_low_strikes;
  portEXIT_CRITICAL(&s_lock);
  return strikes;
}

static void health_mark_metrics_logged(uint64_t now_us) {
  portENTER_CRITICAL(&s_lock);
  s_state.last_metrics_log_us = now_us;
  portEXIT_CRITICAL(&s_lock);
}

static bool health_mark_wifi_recovery_attempt(uint64_t now_us, bool count_attempt,
                                              uint32_t *attempt_out) {
  bool allowed = false;
  portENTER_CRITICAL(&s_lock);
  if (s_state.started) {
    if (now_us - s_state.last_wifi_recovery_us >= UL_HEALTH_WIFI_RECOVERY_RETRY_US) {
      if (!count_attempt || s_state.wifi_recovery_attempts < UL_HEALTH_WIFI_MAX_RECOVERIES) {
        if (count_attempt && s_state.wifi_recovery_attempts < UINT32_MAX)
          s_state.wifi_recovery_attempts++;
        s_state.last_wifi_recovery_us = now_us;
        allowed = true;
      }
    }
    if (attempt_out)
      *attempt_out = s_state.wifi_recovery_attempts;
  }
  portEXIT_CRITICAL(&s_lock);
  return allowed;
}

static bool health_mark_mqtt_recovery_attempt(uint64_t now_us, uint32_t *attempt_out) {
  bool allowed = false;
  portENTER_CRITICAL(&s_lock);
  if (s_state.started) {
    if (now_us - s_state.last_mqtt_recovery_us >= UL_HEALTH_MQTT_RECOVERY_RETRY_US &&
        s_state.mqtt_recovery_attempts < UL_HEALTH_MQTT_MAX_RECOVERIES) {
      if (s_state.mqtt_recovery_attempts < UINT32_MAX)
        s_state.mqtt_recovery_attempts++;
      s_state.last_mqtt_recovery_us = now_us;
      allowed = true;
    }
    if (attempt_out)
      *attempt_out = s_state.mqtt_recovery_attempts;
  }
  portEXIT_CRITICAL(&s_lock);
  return allowed;
}

static void log_metrics(const ul_health_state_t *state, uint64_t now_us,
                        size_t free_heap, size_t min_heap) {
  unsigned long long uptime_s = now_us / 1000000ULL;
  unsigned long long wifi_offline_s = state->wifi_connected
                                          ? 0ULL
                                          : (now_us - state->wifi_last_change_us) / 1000000ULL;
  unsigned long long mqtt_offline_s = state->mqtt_connected
                                          ? 0ULL
                                          : (now_us - state->mqtt_last_change_us) / 1000000ULL;
  unsigned long long since_sync_s = state->time_sync_seen
                                        ? (now_us - state->last_time_sync_us) / 1000000ULL
                                        : uptime_s;
  ESP_LOGI(TAG,
           "Uptime=%llus heap=%u(min=%u) wifi=%s offline=%llus attempts=%u mqtt=%s offline=%llus attempts=%u last_sync=%llus",
           uptime_s, (unsigned)free_heap, (unsigned)min_heap,
           state->wifi_connected ? "up" : "down", wifi_offline_s,
           (unsigned)state->wifi_recovery_attempts,
           state->mqtt_connected ? "up" : "down", mqtt_offline_s,
           (unsigned)state->mqtt_recovery_attempts, since_sync_s);
}

static void health_task(void *arg) {
  (void)arg;
  while (1) {
    vTaskDelay(pdMS_TO_TICKS(UL_HEALTH_MONITOR_PERIOD_MS));

    ul_health_state_t snapshot;
    ul_health_config_t cfg;
    health_take_snapshot(&snapshot, &cfg);
    if (!snapshot.started)
      continue;

    uint64_t now_us = esp_timer_get_time();
    size_t free_heap = esp_get_free_heap_size();
    size_t min_heap = esp_get_minimum_free_heap_size();

    if (now_us - snapshot.last_metrics_log_us >= UL_HEALTH_LOG_INTERVAL_US) {
      log_metrics(&snapshot, now_us, free_heap, min_heap);
      health_mark_metrics_logged(now_us);
    }

    bool heap_low = min_heap < UL_HEALTH_HEAP_LOW_THRESHOLD;
    uint32_t heap_strikes = health_update_heap_low(heap_low);
    if (heap_low && heap_strikes >= UL_HEALTH_HEAP_LOW_STRIKES) {
      ESP_LOGE(TAG, "Heap low for %u consecutive checks (min=%u). Rebooting.",
               heap_strikes, (unsigned)min_heap);
      esp_restart();
    }

    if (!snapshot.wifi_connected) {
      uint64_t offline_us = now_us - snapshot.wifi_last_change_us;
      if (offline_us >= UL_HEALTH_WIFI_RECOVERY_DELAY_US) {
        uint32_t attempt_no = snapshot.wifi_recovery_attempts;
        if (health_mark_wifi_recovery_attempt(now_us, true, &attempt_no)) {
          ESP_LOGW(TAG,
                   "Wi-Fi offline for %llus; requesting recovery attempt #%u",
                   offline_us / 1000000ULL, attempt_no);
          if (cfg.request_wifi_recovery)
            cfg.request_wifi_recovery(cfg.ctx);
          snapshot.wifi_recovery_attempts = attempt_no;
          snapshot.last_wifi_recovery_us = now_us;
        } else if (snapshot.wifi_recovery_attempts >= UL_HEALTH_WIFI_MAX_RECOVERIES &&
                   offline_us >= UL_HEALTH_WIFI_ESCALATE_US &&
                   now_us - snapshot.last_wifi_recovery_us >=
                       UL_HEALTH_WIFI_RECOVERY_RETRY_US) {
          ESP_LOGE(TAG,
                   "Wi-Fi offline %llus despite %u recoveries; rebooting node",
                   offline_us / 1000000ULL,
                   (unsigned)snapshot.wifi_recovery_attempts);
          esp_restart();
        }
      }
      continue;
    }

    if (!snapshot.mqtt_connected) {
      uint64_t mqtt_offline_us = now_us - snapshot.mqtt_last_change_us;
      if (mqtt_offline_us >= UL_HEALTH_MQTT_RECOVERY_DELAY_US) {
        uint32_t attempt_no = snapshot.mqtt_recovery_attempts;
        if (health_mark_mqtt_recovery_attempt(now_us, &attempt_no)) {
          ESP_LOGW(TAG,
                   "MQTT offline for %llus; requesting client restart #%u",
                   mqtt_offline_us / 1000000ULL, attempt_no);
          if (cfg.request_mqtt_recovery)
            cfg.request_mqtt_recovery(cfg.ctx);
          snapshot.mqtt_recovery_attempts = attempt_no;
          snapshot.last_mqtt_recovery_us = now_us;
        } else if (snapshot.mqtt_recovery_attempts >= UL_HEALTH_MQTT_MAX_RECOVERIES &&
                   mqtt_offline_us >= UL_HEALTH_MQTT_ESCALATE_US) {
          uint32_t wifi_attempt = snapshot.wifi_recovery_attempts;
          if (health_mark_wifi_recovery_attempt(now_us, true, &wifi_attempt)) {
            ESP_LOGW(TAG,
                     "MQTT offline %llus after %u restarts; cycling Wi-Fi #%u",
                     mqtt_offline_us / 1000000ULL,
                     (unsigned)snapshot.mqtt_recovery_attempts, wifi_attempt);
            if (cfg.request_wifi_recovery)
              cfg.request_wifi_recovery(cfg.ctx);
            snapshot.wifi_recovery_attempts = wifi_attempt;
            snapshot.last_wifi_recovery_us = now_us;
          }
        }
      }
    }

    bool sntp_running = ul_core_is_sntp_resync_active();
    uint32_t sntp_failures = ul_core_get_sntp_retry_attempts();
    if (!sntp_running && sntp_failures > 0) {
      uint64_t first_failure_us = ul_core_get_sntp_first_failure_us();
      uint64_t last_failure_us = ul_core_get_sntp_last_failure_us();
      uint64_t failing_for_us = 0;
      if (first_failure_us && now_us > first_failure_us)
        failing_for_us = now_us - first_failure_us;
      uint64_t since_last_attempt_us = 0;
      if (last_failure_us && now_us > last_failure_us)
        since_last_attempt_us = now_us - last_failure_us;
      if (sntp_failures != s_last_sntp_retry_log_count ||
          now_us - s_last_sntp_retry_log_us >= UL_HEALTH_LOG_INTERVAL_US) {
        ESP_LOGW(TAG,
                 "SNTP resync task creation failed %u time%s (failing for %llus, last attempt %llus ago)",
                 (unsigned)sntp_failures, sntp_failures == 1 ? "" : "s",
                 failing_for_us / 1000000ULL,
                 since_last_attempt_us / 1000000ULL);
        s_last_sntp_retry_log_count = sntp_failures;
        s_last_sntp_retry_log_us = now_us;
      }
    } else {
      s_last_sntp_retry_log_count = 0;
      s_last_sntp_retry_log_us = 0;
    }

    if (snapshot.time_sync_seen) {
      uint64_t since_sync_us = now_us - snapshot.last_time_sync_us;
      if (since_sync_us >= UL_HEALTH_TIME_SYNC_ERROR_US) {
        ESP_LOGE(TAG, "No SNTP sync for %llus; rebooting", since_sync_us / 1000000ULL);
        esp_restart();
      } else if (since_sync_us >= UL_HEALTH_TIME_SYNC_WARN_US) {
        ESP_LOGW(TAG, "No SNTP sync for %llus", since_sync_us / 1000000ULL);
        uint32_t wifi_attempt = snapshot.wifi_recovery_attempts;
        if (health_mark_wifi_recovery_attempt(now_us, true, &wifi_attempt)) {
          ESP_LOGW(TAG, "Requesting Wi-Fi recovery #%u to restore SNTP", wifi_attempt);
          if (cfg.request_wifi_recovery)
            cfg.request_wifi_recovery(cfg.ctx);
          snapshot.wifi_recovery_attempts = wifi_attempt;
          snapshot.last_wifi_recovery_us = now_us;
        }
      }
    } else if (now_us - snapshot.last_time_sync_us >= UL_HEALTH_TIME_SYNC_WARN_US) {
      ESP_LOGW(TAG, "Awaiting initial SNTP sync (%llus since boot)",
               (now_us - snapshot.last_time_sync_us) / 1000000ULL);
    }
  }
}

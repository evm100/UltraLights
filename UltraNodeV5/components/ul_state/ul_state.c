#include "ul_state.h"

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "ul_task.h"

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define UL_STATE_WS_MAX_STRIPS 2
#define UL_STATE_RGB_MAX_STRIPS 4
#define UL_STATE_WHITE_MAX_CHANNELS 4
#define UL_STATE_RELAY_MAX_CHANNELS 4

#define UL_STATE_MAX_PAYLOAD UL_STATE_MAX_JSON_LEN
#define UL_STATE_FLUSH_DELAY_US (3ULL * 1000000ULL)


static const char *TAG = "ul_state";

typedef enum {
  UL_STATE_TARGET_WS,
  UL_STATE_TARGET_RGB,
  UL_STATE_TARGET_WHITE,
  UL_STATE_TARGET_RELAY,
} ul_state_target_t;

typedef struct {
  ul_state_target_t target;
  int index;
  char key[8];
  esp_timer_handle_t timer;
  char *payload;
  size_t payload_len;
  bool dirty;
} ul_state_entry_t;

static ul_state_entry_t s_entries[UL_STATE_WS_MAX_STRIPS +
                                  UL_STATE_RGB_MAX_STRIPS +
                                  UL_STATE_WHITE_MAX_CHANNELS +
                                  UL_STATE_RELAY_MAX_CHANNELS];
static size_t s_entry_count;

typedef struct {
  int entry_index;
} ul_state_msg_t;

static QueueHandle_t s_queue;
static TaskHandle_t s_task;
static nvs_handle_t s_nvs;
static bool s_ready = false;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;

static void schedule_flush(size_t entry_index);
static bool copy_entry(size_t entry_index, char *buffer, size_t buffer_len);
static void cleanup_resources(size_t active_entries);

static void ul_state_task(void *arg) {
  ul_state_msg_t msg;
  while (xQueueReceive(s_queue, &msg, portMAX_DELAY) == pdTRUE) {
    if (msg.entry_index < 0 || msg.entry_index >= (int)s_entry_count)
      continue;
    ul_state_entry_t *entry = &s_entries[msg.entry_index];

    char *copy = NULL;
    size_t len = 0;

    portENTER_CRITICAL(&s_lock);
    if (entry->dirty && entry->payload && entry->payload_len > 0) {
      len = entry->payload_len;
      copy = malloc(len);
      if (copy) {
        memcpy(copy, entry->payload, len);
        entry->dirty = false;
      }
    }
    portEXIT_CRITICAL(&s_lock);

    if (!copy) {
      schedule_flush(msg.entry_index);
      continue;
    }

    esp_err_t err = nvs_set_blob(s_nvs, entry->key, copy, len);
    if (err == ESP_OK) {
      err = nvs_commit(s_nvs);
    }

    if (err != ESP_OK) {
      ESP_LOGE(TAG, "Failed to persist %s: %s", entry->key,
               esp_err_to_name(err));
      portENTER_CRITICAL(&s_lock);
      entry->dirty = true;
      portEXIT_CRITICAL(&s_lock);
      schedule_flush(msg.entry_index);
    } else {
      ESP_LOGD(TAG, "Persisted %s (%u bytes)", entry->key,
               (unsigned)len);
    }

    free(copy);
  }
}

static void flush_timer_cb(void *arg) {
  int entry_index = (int)(intptr_t)arg;
  bool dirty = false;
  portENTER_CRITICAL(&s_lock);
  if (entry_index >= 0 && entry_index < (int)s_entry_count) {
    dirty = s_entries[entry_index].dirty;
  }
  portEXIT_CRITICAL(&s_lock);
  if (!dirty)
    return;
  if (!s_queue)
    return;
  ul_state_msg_t msg = {.entry_index = entry_index};
  if (xQueueSend(s_queue, &msg, 0) != pdPASS) {
    ESP_LOGW(TAG, "Persistence queue full; delaying request for %d",
             entry_index);
    schedule_flush(entry_index);
  }
}

static void cleanup_entry(ul_state_entry_t *entry) {
  if (!entry)
    return;
  if (entry->timer) {
    esp_err_t err = esp_timer_stop(entry->timer);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
      ESP_LOGW(TAG, "Failed to stop timer for %s: %s",
               entry->key[0] ? entry->key : "entry",
               esp_err_to_name(err));
    }
    err = esp_timer_delete(entry->timer);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "Failed to delete timer for %s: %s",
               entry->key[0] ? entry->key : "entry",
               esp_err_to_name(err));
    }
    entry->timer = NULL;
  }
  if (entry->payload) {
    free(entry->payload);
    entry->payload = NULL;
  }
  entry->payload_len = 0;
  entry->dirty = false;
}

static void cleanup_resources(size_t active_entries) {
  size_t total_entries = sizeof(s_entries) / sizeof(s_entries[0]);
  if (active_entries > total_entries)
    active_entries = total_entries;
  for (size_t i = 0; i < active_entries; ++i) {
    cleanup_entry(&s_entries[i]);
  }
  if (s_queue) {
    vQueueDelete(s_queue);
    s_queue = NULL;
  }
  if (s_nvs) {
    nvs_close(s_nvs);
    s_nvs = 0;
  }
  s_entry_count = 0;
  s_task = NULL;
  s_ready = false;
}

static esp_err_t init_entry(size_t entry_index, ul_state_target_t target,
                            int index, const char *key) {
  ul_state_entry_t *entry = &s_entries[entry_index];
  entry->target = target;
  entry->index = index;
  strncpy(entry->key, key, sizeof(entry->key));
  entry->key[sizeof(entry->key) - 1] = 0;
  entry->payload = NULL;
  entry->payload_len = 0;
  entry->dirty = false;
  entry->timer = NULL;

  const esp_timer_create_args_t args = {
      .callback = &flush_timer_cb,
      .arg = (void *)(intptr_t)entry_index,
      .name = "ul_state", };
  esp_err_t err = esp_timer_create(&args, &entry->timer);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to create persistence timer for %s: %s", entry->key,
             esp_err_to_name(err));
    cleanup_resources(entry_index);
    return err;
  }
  return ESP_OK;
}

esp_err_t ul_state_init(void) {
  if (s_ready)
    return ESP_OK;

  s_entry_count = 0;
  s_task = NULL;
  s_queue = NULL;
  s_nvs = 0;

  esp_err_t err = nvs_open("ulstate", NVS_READWRITE, &s_nvs);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to open NVS namespace: %s", esp_err_to_name(err));
    return err;
  }

  s_queue = xQueueCreate(8, sizeof(ul_state_msg_t));
  if (!s_queue) {
    ESP_LOGE(TAG, "Failed to create persistence queue");
    cleanup_resources(0);
    return ESP_ERR_NO_MEM;
  }

  const size_t total_entries = sizeof(s_entries) / sizeof(s_entries[0]);

  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WS, 0, "ws0")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WS, 1, "ws1")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RGB, 0, "rgb0")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RGB, 1, "rgb1")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RGB, 2, "rgb2")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RGB, 3, "rgb3")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WHITE, 0, "wht0")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WHITE, 1, "wht1")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WHITE, 2, "wht2")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_WHITE, 3, "wht3")) !=
      ESP_OK)
    return err;
  s_entry_count++;

  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RELAY, 0, "rly0")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RELAY, 1, "rly1")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RELAY, 2, "rly2")) !=
      ESP_OK)
    return err;
  s_entry_count++;
  if ((err = init_entry(s_entry_count, UL_STATE_TARGET_RELAY, 3, "rly3")) !=
      ESP_OK)
    return err;
  s_entry_count++;

  if (s_entry_count > total_entries) {
    ESP_LOGE(TAG, "Too many state entries configured");
    cleanup_resources(s_entry_count);
    return ESP_FAIL;
  }

  if (ul_task_create(ul_state_task, "ul_state", 4096, NULL, 5, &s_task, 0) !=
      pdPASS) {
    ESP_LOGE(TAG, "Failed to start persistence task");
    cleanup_resources(s_entry_count);
    return ESP_ERR_NO_MEM;
  }

  s_ready = true;
  return ESP_OK;
}

static void schedule_flush(size_t entry_index) {
  if (!s_ready)
    return;
  if (entry_index >= s_entry_count)
    return;
  ul_state_entry_t *entry = &s_entries[entry_index];
  if (!entry->timer)
    return;
  esp_err_t err = esp_timer_stop(entry->timer);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(TAG, "Failed to stop timer for %u: %s", (unsigned)entry_index,
             esp_err_to_name(err));
  }
  err = esp_timer_start_once(entry->timer, UL_STATE_FLUSH_DELAY_US);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to arm timer for %u: %s", (unsigned)entry_index,
             esp_err_to_name(err));
  }
}

static void update_entry(size_t entry_index, const char *payload, size_t len) {
  if (!s_ready || !payload)
    return;
  if (entry_index >= s_entry_count)
    return;
  if (len + 1 > UL_STATE_MAX_PAYLOAD) {
    ESP_LOGW(TAG, "Payload too large for persistence (%u bytes)",
             (unsigned)(len + 1));
    return;
  }

  char *copy = malloc(len + 1);
  if (!copy)
    return;
  memcpy(copy, payload, len);
  copy[len] = '\0';

  ul_state_entry_t *entry = &s_entries[entry_index];
  char *old_payload = NULL;

  portENTER_CRITICAL(&s_lock);
  if (entry->payload && entry->payload_len == len + 1 &&
      memcmp(entry->payload, copy, len + 1) == 0) {
    portEXIT_CRITICAL(&s_lock);
    free(copy);
    return;
  }
  old_payload = entry->payload;
  entry->payload = copy;
  entry->payload_len = len + 1;
  entry->dirty = true;
  portEXIT_CRITICAL(&s_lock);

  if (old_payload)
    free(old_payload);

  schedule_flush(entry_index);
}

static bool copy_entry(size_t entry_index, char *buffer, size_t buffer_len) {
  if (!buffer || buffer_len == 0)
    return false;

  buffer[0] = '\0';

  if (!s_ready)
    return false;
  if (entry_index >= s_entry_count)
    return false;

  bool copied = false;

  portENTER_CRITICAL(&s_lock);
  ul_state_entry_t *entry = &s_entries[entry_index];
  if (entry->payload && entry->payload_len > 0 &&
      entry->payload_len <= buffer_len) {
    memcpy(buffer, entry->payload, entry->payload_len);
    copied = true;
  }
  portEXIT_CRITICAL(&s_lock);

  if (!copied)
    buffer[0] = '\0';

  return copied;
}

void ul_state_record_ws(int strip, const char *payload, size_t len) {
  if (strip < 0 || strip >= UL_STATE_WS_MAX_STRIPS)
    return;
  update_entry(strip, payload, len);
}

void ul_state_record_rgb(int strip, const char *payload, size_t len) {
  if (strip < 0 || strip >= UL_STATE_RGB_MAX_STRIPS)
    return;
  update_entry(UL_STATE_WS_MAX_STRIPS + strip, payload, len);
}

void ul_state_record_white(int channel, const char *payload, size_t len) {
  if (channel < 0 || channel >= UL_STATE_WHITE_MAX_CHANNELS)
    return;
  size_t base = UL_STATE_WS_MAX_STRIPS + UL_STATE_RGB_MAX_STRIPS;
  update_entry(base + channel, payload, len);
}

void ul_state_record_relay(int channel, const char *payload, size_t len) {
  if (channel < 0 || channel >= UL_STATE_RELAY_MAX_CHANNELS)
    return;
  size_t base = UL_STATE_WS_MAX_STRIPS + UL_STATE_RGB_MAX_STRIPS +
                UL_STATE_WHITE_MAX_CHANNELS;
  update_entry(base + channel, payload, len);
}

bool ul_state_copy_ws(int strip, char *buffer, size_t buffer_len) {
  if (strip < 0 || strip >= UL_STATE_WS_MAX_STRIPS) {
    if (buffer && buffer_len > 0)
      buffer[0] = '\0';
    return false;
  }
  return copy_entry(strip, buffer, buffer_len);
}

bool ul_state_copy_rgb(int strip, char *buffer, size_t buffer_len) {
  if (strip < 0 || strip >= UL_STATE_RGB_MAX_STRIPS) {
    if (buffer && buffer_len > 0)
      buffer[0] = '\0';
    return false;
  }
  return copy_entry(UL_STATE_WS_MAX_STRIPS + strip, buffer, buffer_len);
}

bool ul_state_copy_white(int channel, char *buffer, size_t buffer_len) {
  if (channel < 0 || channel >= UL_STATE_WHITE_MAX_CHANNELS) {
    if (buffer && buffer_len > 0)
      buffer[0] = '\0';
    return false;
  }
  size_t base = UL_STATE_WS_MAX_STRIPS + UL_STATE_RGB_MAX_STRIPS;
  return copy_entry(base + channel, buffer, buffer_len);
}

bool ul_state_copy_relay(int channel, char *buffer, size_t buffer_len) {
  if (channel < 0 || channel >= UL_STATE_RELAY_MAX_CHANNELS) {
    if (buffer && buffer_len > 0)
      buffer[0] = '\0';
    return false;
  }
  size_t base = UL_STATE_WS_MAX_STRIPS + UL_STATE_RGB_MAX_STRIPS +
                UL_STATE_WHITE_MAX_CHANNELS;
  return copy_entry(base + channel, buffer, buffer_len);
}

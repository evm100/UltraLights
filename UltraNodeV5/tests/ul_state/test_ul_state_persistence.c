#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "ul_task.h"

// ---- Stub state -------------------------------------------------------------

typedef struct {
  size_t length;
  size_t item_size;
  int send_calls;
  int receive_calls;
} queue_stub_t;

static int g_queue_create_calls = 0;
static int g_queue_delete_calls = 0;
static int g_queue_send_calls = 0;
static int g_queue_receive_calls = 0;
static bool g_queue_create_fail = false;

static int g_ul_task_create_calls = 0;
static bool g_ul_task_create_should_fail = false;

static int g_nvs_open_calls = 0;
static int g_nvs_close_calls = 0;
static int g_nvs_set_blob_calls = 0;
static int g_nvs_commit_calls = 0;
static bool g_nvs_open_should_fail = false;
static esp_err_t g_nvs_open_fail_err = ESP_FAIL;

static int g_esp_timer_create_calls = 0;
static int g_esp_timer_delete_calls = 0;
static int g_esp_timer_start_calls = 0;
static int g_esp_timer_stop_calls = 0;
static int g_esp_timer_create_fail_at = -1;
static esp_err_t g_esp_timer_create_fail_err = ESP_ERR_NO_MEM;
static uint64_t g_fake_time_us = 0;

typedef struct {
  bool active;
  esp_timer_cb_t cb;
  void *arg;
  const char *name;
} timer_stub_t;

// ---- Stub helpers -----------------------------------------------------------

const char *esp_err_to_name(esp_err_t err) {
  switch (err) {
  case ESP_OK:
    return "ESP_OK";
  case ESP_ERR_NO_MEM:
    return "ESP_ERR_NO_MEM";
  case ESP_ERR_INVALID_STATE:
    return "ESP_ERR_INVALID_STATE";
  case ESP_FAIL:
    return "ESP_FAIL";
  default:
    return "ESP_ERR_UNKNOWN";
  }
}

QueueHandle_t xQueueCreate(UBaseType_t length, UBaseType_t item_size) {
  g_queue_create_calls++;
  if (g_queue_create_fail)
    return NULL;
  queue_stub_t *queue = calloc(1, sizeof(*queue));
  if (!queue)
    return NULL;
  queue->length = length;
  queue->item_size = item_size;
  return queue;
}

BaseType_t xQueueSend(QueueHandle_t queue, const void *item, TickType_t ticks) {
  (void)item;
  (void)ticks;
  g_queue_send_calls++;
  if (!queue)
    return pdFAIL;
  queue_stub_t *stub = (queue_stub_t *)queue;
  stub->send_calls++;
  return pdPASS;
}

BaseType_t xQueueReceive(QueueHandle_t queue, void *item, TickType_t ticks) {
  (void)queue;
  (void)item;
  (void)ticks;
  g_queue_receive_calls++;
  return pdFALSE;
}

void vQueueDelete(QueueHandle_t queue) {
  g_queue_delete_calls++;
  free(queue);
}

BaseType_t ul_task_create(TaskFunction_t task_func, const char *name,
                          const uint32_t stack_depth, void *params,
                          UBaseType_t priority, TaskHandle_t *task_handle,
                          BaseType_t core_id) {
  (void)task_func;
  (void)name;
  (void)stack_depth;
  (void)params;
  (void)priority;
  (void)core_id;
  g_ul_task_create_calls++;
  if (g_ul_task_create_should_fail)
    return pdFAIL;
  if (task_handle)
    *task_handle = (TaskHandle_t)0x1;
  return pdPASS;
}

esp_err_t nvs_open(const char *name, nvs_open_mode_t open_mode,
                   nvs_handle_t *out_handle) {
  (void)name;
  (void)open_mode;
  g_nvs_open_calls++;
  if (g_nvs_open_should_fail) {
    if (out_handle)
      *out_handle = NULL;
    return g_nvs_open_fail_err;
  }
  if (out_handle)
    *out_handle = (nvs_handle_t)0x1;
  return ESP_OK;
}

void nvs_close(nvs_handle_t handle) {
  (void)handle;
  g_nvs_close_calls++;
}

esp_err_t nvs_set_blob(nvs_handle_t handle, const char *key, const void *value,
                       size_t length) {
  (void)handle;
  (void)key;
  (void)value;
  (void)length;
  g_nvs_set_blob_calls++;
  return ESP_OK;
}

esp_err_t nvs_commit(nvs_handle_t handle) {
  (void)handle;
  g_nvs_commit_calls++;
  return ESP_OK;
}

TickType_t xTaskGetTickCount(void) {
  static TickType_t fake_ticks;
  return fake_ticks++;
}

void vTaskDelayUntil(TickType_t *const previous, TickType_t increment) {
  if (previous)
    *previous += increment;
}

void vTaskDelay(TickType_t ticks) {
  (void)ticks;
}

void vTaskDelete(TaskHandle_t task) {
  (void)task;
}

esp_err_t esp_timer_create(const esp_timer_create_args_t *args,
                           esp_timer_handle_t *out_handle) {
  g_esp_timer_create_calls++;
  if (g_esp_timer_create_fail_at > 0 &&
      g_esp_timer_create_calls == g_esp_timer_create_fail_at) {
    if (out_handle)
      *out_handle = NULL;
    return g_esp_timer_create_fail_err;
  }
  timer_stub_t *timer = calloc(1, sizeof(*timer));
  if (!timer)
    return ESP_ERR_NO_MEM;
  if (args) {
    timer->cb = args->callback;
    timer->arg = args->arg;
    timer->name = args->name;
  }
  if (out_handle)
    *out_handle = timer;
  return ESP_OK;
}

esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout_us) {
  (void)timeout_us;
  g_esp_timer_start_calls++;
  if (!timer)
    return ESP_FAIL;
  timer_stub_t *stub = (timer_stub_t *)timer;
  stub->active = true;
  return ESP_OK;
}

esp_err_t esp_timer_stop(esp_timer_handle_t timer) {
  g_esp_timer_stop_calls++;
  if (!timer)
    return ESP_ERR_INVALID_STATE;
  timer_stub_t *stub = (timer_stub_t *)timer;
  if (!stub->active)
    return ESP_ERR_INVALID_STATE;
  stub->active = false;
  return ESP_OK;
}

esp_err_t esp_timer_delete(esp_timer_handle_t timer) {
  g_esp_timer_delete_calls++;
  free(timer);
  return ESP_OK;
}

bool esp_timer_is_active(esp_timer_handle_t timer) {
  if (!timer)
    return false;
  return ((timer_stub_t *)timer)->active;
}

uint64_t esp_timer_get_time(void) {
  g_fake_time_us += 1000;
  return g_fake_time_us;
}

static void reset_test_state(void) {
  g_queue_create_calls = 0;
  g_queue_delete_calls = 0;
  g_queue_send_calls = 0;
  g_queue_receive_calls = 0;
  g_queue_create_fail = false;

  g_ul_task_create_calls = 0;
  g_ul_task_create_should_fail = false;

  g_nvs_open_calls = 0;
  g_nvs_close_calls = 0;
  g_nvs_set_blob_calls = 0;
  g_nvs_commit_calls = 0;
  g_nvs_open_should_fail = false;
  g_nvs_open_fail_err = ESP_FAIL;

  g_esp_timer_create_calls = 0;
  g_esp_timer_delete_calls = 0;
  g_esp_timer_start_calls = 0;
  g_esp_timer_stop_calls = 0;
  g_esp_timer_create_fail_at = -1;
  g_esp_timer_create_fail_err = ESP_ERR_NO_MEM;
  g_fake_time_us = 0;
}

#include "../../components/ul_state/ul_state.c"

#define TOTAL_ENTRIES                                                     \
  (UL_STATE_WS_MAX_STRIPS + UL_STATE_RGB_MAX_STRIPS +                    \
   UL_STATE_WHITE_MAX_CHANNELS)

static void test_timer_create_failure(void) {
  reset_test_state();

  g_esp_timer_create_fail_at = 3;
  esp_err_t err = ul_state_init();
  assert(err == g_esp_timer_create_fail_err);
  assert(g_esp_timer_create_calls == 3);
  assert(g_esp_timer_delete_calls == 2);
  assert(g_queue_create_calls == 1);
  assert(g_queue_delete_calls == 1);
  assert(g_nvs_open_calls == 1);
  assert(g_nvs_close_calls == 1);
  assert(g_ul_task_create_calls == 0);

  const char payload[] = "{\"mode\":1}";
  ul_state_record_ws(0, payload, strlen(payload));
  assert(g_esp_timer_start_calls == 0);
  assert(g_queue_send_calls == 0);

  g_esp_timer_create_fail_at = -1;
  int prev_timer_creates = g_esp_timer_create_calls;
  int prev_queue_creates = g_queue_create_calls;
  int prev_nvs_opens = g_nvs_open_calls;

  err = ul_state_init();
  assert(err == ESP_OK);
  assert(g_esp_timer_create_calls == prev_timer_creates + TOTAL_ENTRIES);
  assert(g_queue_create_calls == prev_queue_creates + 1);
  assert(g_nvs_open_calls == prev_nvs_opens + 1);
  assert(g_ul_task_create_calls == 1);

  ul_state_record_ws(0, payload, strlen(payload));
  assert(g_esp_timer_start_calls > 0);

  printf("All tests passed\n");
}

int main(void) {
  test_timer_create_failure();
  return 0;
}

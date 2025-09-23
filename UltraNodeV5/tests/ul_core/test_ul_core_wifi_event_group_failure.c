#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "sdkconfig.h"
#include "ul_task.h"

struct EventGroupStub {
  EventBits_t bits;
};

typedef struct {
  bool deleted;
} stub_semaphore_t;

typedef struct {
  bool active;
  esp_timer_cb_t cb;
} stub_timer_t;

static bool g_force_event_group_failure;
static int g_event_group_create_calls;
static int g_event_group_wait_calls;
static int g_event_group_set_calls;
static int g_event_group_clear_calls;
static int g_event_group_delete_calls;
static int g_timer_stop_calls;
static int g_timer_delete_calls;
static int g_mutex_delete_calls;
static int g_wifi_connect_calls;

static struct EventGroupStub g_event_group_instance;
static stub_semaphore_t g_mutex_instance;
static stub_semaphore_t g_binary_semaphore_instance;
static stub_timer_t g_existing_timer;
static stub_timer_t g_created_timer;

EventGroupHandle_t xEventGroupCreate(void) {
  g_event_group_create_calls++;
  if (g_force_event_group_failure)
    return NULL;
  g_event_group_instance.bits = 0;
  return &g_event_group_instance;
}

EventBits_t xEventGroupWaitBits(EventGroupHandle_t event_group,
                                EventBits_t bits_to_wait_for,
                                BaseType_t clear_on_exit,
                                BaseType_t wait_for_all_bits,
                                TickType_t ticks_to_wait) {
  (void)clear_on_exit;
  (void)wait_for_all_bits;
  (void)ticks_to_wait;
  g_event_group_wait_calls++;
  if (!event_group)
    return 0;
  EventBits_t value = event_group->bits & bits_to_wait_for;
  if (clear_on_exit)
    event_group->bits &= ~bits_to_wait_for;
  return value;
}

EventBits_t xEventGroupSetBits(EventGroupHandle_t event_group,
                               EventBits_t bits_to_set) {
  g_event_group_set_calls++;
  if (!event_group)
    return 0;
  event_group->bits |= bits_to_set;
  return event_group->bits;
}

EventBits_t xEventGroupClearBits(EventGroupHandle_t event_group,
                                 EventBits_t bits_to_clear) {
  g_event_group_clear_calls++;
  if (!event_group)
    return 0;
  event_group->bits &= ~bits_to_clear;
  return event_group->bits;
}

EventBits_t xEventGroupGetBits(EventGroupHandle_t event_group) {
  if (!event_group)
    return 0;
  return event_group->bits;
}

void vEventGroupDelete(EventGroupHandle_t event_group) {
  if (event_group)
    g_event_group_delete_calls++;
}

SemaphoreHandle_t xSemaphoreCreateBinary(void) {
  g_binary_semaphore_instance.deleted = false;
  return &g_binary_semaphore_instance;
}

SemaphoreHandle_t xSemaphoreCreateMutex(void) {
  g_mutex_instance.deleted = false;
  return &g_mutex_instance;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) {
  (void)sem;
  (void)ticks;
  return pdTRUE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t sem) {
  (void)sem;
  return pdTRUE;
}

void vSemaphoreDelete(SemaphoreHandle_t sem) {
  if (sem) {
    ((stub_semaphore_t *)sem)->deleted = true;
    g_mutex_delete_calls++;
  }
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

BaseType_t ul_task_create(TaskFunction_t task_func, const char *name,
                          const uint32_t stack_depth, void *params,
                          UBaseType_t priority, TaskHandle_t *task_handle,
                          BaseType_t core_id) {
  (void)task_func;
  (void)name;
  (void)stack_depth;
  (void)params;
  (void)priority;
  (void)task_handle;
  (void)core_id;
  return pdPASS;
}

esp_err_t esp_timer_create(const esp_timer_create_args_t *args,
                           esp_timer_handle_t *out_handle) {
  g_created_timer.active = false;
  g_created_timer.cb = args ? args->callback : NULL;
  if (out_handle)
    *out_handle = &g_created_timer;
  return ESP_OK;
}

esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout_us) {
  (void)timeout_us;
  if (timer)
    ((stub_timer_t *)timer)->active = true;
  return ESP_OK;
}

esp_err_t esp_timer_stop(esp_timer_handle_t timer) {
  g_timer_stop_calls++;
  if (timer)
    ((stub_timer_t *)timer)->active = false;
  return ESP_OK;
}

esp_err_t esp_timer_delete(esp_timer_handle_t timer) {
  (void)timer;
  g_timer_delete_calls++;
  return ESP_OK;
}

bool esp_timer_is_active(esp_timer_handle_t timer) {
  if (!timer)
    return false;
  return ((stub_timer_t *)timer)->active;
}

uint64_t esp_timer_get_time(void) {
  static uint64_t now;
  return now += 1000;
}

esp_err_t esp_event_handler_register(esp_event_base_t event_base,
                                     int32_t event_id,
                                     esp_event_handler_t event_handler,
                                     void *event_handler_arg) {
  (void)event_base;
  (void)event_id;
  (void)event_handler;
  (void)event_handler_arg;
  return ESP_OK;
}

esp_err_t esp_event_handler_unregister(esp_event_base_t event_base,
                                       int32_t event_id,
                                       esp_event_handler_t event_handler) {
  (void)event_base;
  (void)event_id;
  (void)event_handler;
  return ESP_OK;
}

esp_err_t esp_wifi_init(const wifi_init_config_t *cfg) {
  (void)cfg;
  return ESP_OK;
}

esp_err_t esp_wifi_set_mode(int mode) {
  (void)mode;
  return ESP_OK;
}

esp_err_t esp_wifi_set_config(int interface, wifi_config_t *config) {
  (void)interface;
  (void)config;
  return ESP_OK;
}

esp_err_t esp_wifi_start(void) { return ESP_OK; }

esp_err_t esp_wifi_stop(void) { return ESP_OK; }

esp_err_t esp_wifi_deinit(void) { return ESP_OK; }

esp_err_t esp_wifi_connect(void) {
  g_wifi_connect_calls++;
  return ESP_OK;
}

const char *esp_err_to_name(esp_err_t err) {
  return err == ESP_OK ? "ESP_OK" : "ERR";
}

esp_err_t esp_netif_sntp_init(const esp_sntp_config_t *config) {
  (void)config;
  return ESP_OK;
}

esp_err_t esp_netif_sntp_start(void) { return ESP_OK; }

void esp_sntp_set_time_sync_notification_cb(void (*cb)(struct timeval *tv)) {
  (void)cb;
}

void esp_sntp_stop(void) {}

#include "../../components/ul_core/ul_core.c"

static void reset_test_state(void) {
  g_force_event_group_failure = false;
  g_event_group_create_calls = 0;
  g_event_group_wait_calls = 0;
  g_event_group_set_calls = 0;
  g_event_group_clear_calls = 0;
  g_event_group_delete_calls = 0;
  g_timer_stop_calls = 0;
  g_timer_delete_calls = 0;
  g_mutex_delete_calls = 0;
  g_wifi_connect_calls = 0;
  g_event_group_instance.bits = 0;
  g_mutex_instance.deleted = false;
  g_binary_semaphore_instance.deleted = false;
  g_existing_timer.active = false;
  g_existing_timer.cb = NULL;
  g_created_timer.active = false;
  g_created_timer.cb = NULL;
  s_wifi_event_group = NULL;
  s_reconnect_timer = NULL;
  s_wifi_restart_mutex = NULL;
  s_conn_cb = NULL;
  s_time_sync_cb = NULL;
  s_time_sync_ctx = NULL;
  s_sntp_task = NULL;
  if (s_sntp_retry_timer) {
    esp_timer_delete(s_sntp_retry_timer);
    s_sntp_retry_timer = NULL;
  }
  s_sntp_retry_attempts = 0;
  s_sntp_first_failure_us = 0;
  s_sntp_last_failure_us = 0;
  s_sntp_retry_delay_ms = SNTP_RETRY_INITIAL_DELAY_MS;
}

static void test_event_group_create_failure(void) {
  reset_test_state();
  g_force_event_group_failure = true;
  s_reconnect_timer = &g_existing_timer;
  s_wifi_restart_mutex = &g_mutex_instance;

  ul_core_wifi_start();

  assert(g_event_group_create_calls == 1);
  assert(s_wifi_event_group == NULL);
  assert(s_reconnect_timer == NULL);
  assert(s_wifi_restart_mutex == NULL);
  assert(g_timer_stop_calls == 1);
  assert(g_timer_delete_calls == 1);
  assert(g_mutex_delete_calls == 1);
  assert(g_wifi_connect_calls == 0);
  assert(g_event_group_delete_calls == 0);

  bool got_ip = ul_core_wait_for_ip(pdMS_TO_TICKS(100));
  assert(!got_ip);
  assert(g_event_group_wait_calls == 0);
  assert(!ul_core_is_connected());

  wifi_event_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_START, NULL);
  wifi_event_handler(NULL, WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, NULL);
  wifi_event_handler(NULL, IP_EVENT, IP_EVENT_STA_GOT_IP, NULL);
  assert(g_wifi_connect_calls == 0);
  assert(g_event_group_set_calls == 0);
  assert(g_event_group_clear_calls == 0);

  wifi_reconnect_timer_cb(NULL);
  assert(g_event_group_clear_calls == 0);
}

int main(void) {
  test_event_group_create_failure();
  printf("All tests passed\n");
  return 0;
}

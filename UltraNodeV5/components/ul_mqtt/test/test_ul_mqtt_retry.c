#include <assert.h>
#include <stdio.h>

#define UL_MQTT_TESTING 1
#include "ul_mqtt_test_stubs.h"

static bool g_core_connected = true;
static int g_health_notify_calls = 0;
static bool g_health_last_state = true;
static int g_init_calls = 0;
static int g_register_calls = 0;
static int g_register_failures_remaining = 0;
static int g_start_calls = 0;
static int g_stop_calls = 0;
static int g_destroy_calls = 0;
static int g_vtaskdelay_last = -1;
static int g_init_failures_remaining = 1;

struct ul_mqtt_test_client {
  int placeholder;
};

static struct ul_mqtt_test_client g_client = {0};
static esp_event_handler_t g_registered_handler = NULL;

static esp_timer_t g_timer = {0};
static bool g_timer_created = false;

bool ul_core_is_connected(void) { return g_core_connected; }

void ul_health_notify_mqtt(bool connected) {
  g_health_notify_calls++;
  g_health_last_state = connected;
}

esp_err_t esp_timer_create(const esp_timer_create_args_t *args,
                           esp_timer_handle_t *out_handle) {
  g_timer_created = true;
  g_timer.callback = args ? args->callback : NULL;
  g_timer.arg = args ? args->arg : NULL;
  g_timer.active = false;
  g_timer.timeout_us = 0;
  if (out_handle)
    *out_handle = &g_timer;
  return ESP_OK;
}

esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout_us) {
  if (!timer)
    return ESP_FAIL;
  timer->active = true;
  timer->timeout_us = timeout_us;
  return ESP_OK;
}

esp_err_t esp_timer_stop(esp_timer_handle_t timer) {
  if (!timer)
    return ESP_FAIL;
  if (!timer->active)
    return ESP_ERR_INVALID_STATE;
  timer->active = false;
  return ESP_OK;
}

esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *cfg) {
  (void)cfg;
  g_init_calls++;
  if (g_init_failures_remaining > 0) {
    g_init_failures_remaining--;
    return NULL;
  }
  return &g_client;
}

esp_err_t esp_mqtt_client_register_event(esp_mqtt_client_handle_t client,
                                         int32_t event_id,
                                         esp_event_handler_t handler,
                                         void *event_data) {
  (void)client;
  (void)event_id;
  (void)event_data;
  g_register_calls++;
  if (g_register_failures_remaining > 0) {
    g_register_failures_remaining--;
    g_registered_handler = NULL;
    return ESP_FAIL;
  }
  g_registered_handler = handler;
  return ESP_OK;
}

esp_err_t esp_mqtt_client_start(esp_mqtt_client_handle_t client) {
  (void)client;
  g_start_calls++;
  return ESP_OK;
}

esp_err_t esp_mqtt_client_stop(esp_mqtt_client_handle_t client) {
  (void)client;
  g_stop_calls++;
  return ESP_OK;
}

esp_err_t esp_mqtt_client_destroy(esp_mqtt_client_handle_t client) {
  (void)client;
  g_destroy_calls++;
  return ESP_OK;
}

void vTaskDelay(int ticks) { g_vtaskdelay_last = ticks; }

void motion_fade_cancel(void) {}

void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id,
                        void *event_data) {
  (void)handler_args;
  (void)base;
  (void)event_id;
  (void)event_data;
}

#include "../ul_mqtt.c"

static void fire_retry_timer(void) {
  assert(g_timer_created);
  if (g_timer.active && g_timer.callback) {
    esp_timer_cb_t cb = g_timer.callback;
    void *arg = g_timer.arg;
    g_timer.active = false;
    cb(arg);
  }
}

static void reset_metrics(void) {
  g_health_notify_calls = 0;
  g_health_last_state = true;
  g_init_calls = 0;
  g_register_calls = 0;
  g_register_failures_remaining = 0;
  g_start_calls = 0;
  g_stop_calls = 0;
  g_destroy_calls = 0;
  g_vtaskdelay_last = -1;
  g_init_failures_remaining = 0;
  g_registered_handler = NULL;
}

static void test_init_failure_retry(void) {
  reset_metrics();
  g_core_connected = true;
  g_init_failures_remaining = 1;

  ul_mqtt_start();

  assert(g_init_calls == 1);
  assert(ul_mqtt_test_get_client_handle() == NULL);
  assert(g_register_calls == 0);
  assert(g_start_calls == 0);
  assert(g_destroy_calls == 0);
  assert(g_health_notify_calls == 1);
  assert(g_health_last_state == false);
  assert(g_timer_created);
  assert(ul_mqtt_test_retry_pending());
  assert(g_timer.active);

  g_init_failures_remaining = 0;

  fire_retry_timer();

  assert(g_init_calls == 2);
  assert(g_register_calls == 1);
  assert(g_start_calls == 1);
  assert(g_registered_handler != NULL);
  assert(ul_mqtt_test_get_client_handle() == &g_client);
  assert(!ul_mqtt_test_retry_pending());
  assert(!g_timer.active);
  assert(g_health_notify_calls == 2);
  assert(g_health_last_state == false);

  ul_mqtt_stop();
  assert(ul_mqtt_test_get_client_handle() == NULL);
  assert(g_stop_calls == 1);
  assert(g_destroy_calls == 1);
  assert(g_health_notify_calls == 3);
  assert(g_health_last_state == false);
}

static void test_register_failure_retry(void) {
  reset_metrics();
  g_core_connected = true;
  g_register_failures_remaining = 1;

  ul_mqtt_start();

  assert(g_init_calls == 1);
  assert(g_register_calls == 1);
  assert(g_start_calls == 0);
  assert(g_destroy_calls == 1);
  assert(ul_mqtt_test_get_client_handle() == NULL);
  assert(g_registered_handler == NULL);
  assert(g_health_notify_calls == 1);
  assert(g_health_last_state == false);
  assert(ul_mqtt_test_retry_pending());
  assert(g_timer.active);

  fire_retry_timer();

  assert(g_init_calls == 2);
  assert(g_register_calls == 2);
  assert(g_start_calls == 1);
  assert(g_destroy_calls == 1);
  assert(g_registered_handler != NULL);
  assert(ul_mqtt_test_get_client_handle() == &g_client);
  assert(!ul_mqtt_test_retry_pending());
  assert(!g_timer.active);
  assert(g_health_notify_calls == 2);
  assert(g_health_last_state == false);

  ul_mqtt_stop();
  assert(ul_mqtt_test_get_client_handle() == NULL);
  assert(g_stop_calls == 1);
  assert(g_destroy_calls == 2);
  assert(g_health_notify_calls == 3);
  assert(g_health_last_state == false);
}

int main(void) {
  test_init_failure_retry();
  test_register_failure_retry();

  printf("ul_mqtt_retry_test passed\n");
  return 0;
}


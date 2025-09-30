#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

typedef int esp_err_t;

#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_STATE 0x0103

#define ESP_LOGI(tag, fmt, ...) do { (void)(tag); (void)(fmt); } while (0)
#define ESP_LOGW(tag, fmt, ...) do { (void)(tag); (void)(fmt); } while (0)
#define ESP_LOGE(tag, fmt, ...) do { (void)(tag); (void)(fmt); } while (0)
#define ESP_LOGD(tag, fmt, ...) do { (void)(tag); (void)(fmt); } while (0)

#define CONFIG_UL_MQTT_URI "test://broker"
#define CONFIG_UL_MQTT_USER "test_user"
#define CONFIG_UL_MQTT_PASS "test_pass"
#define CONFIG_UL_MQTT_LEGACY_USERPASS_COMPAT 1
#define CONFIG_UL_MQTT_DIAL_HOST ""
#define CONFIG_UL_MQTT_DIAL_PORT 0
#define CONFIG_UL_MQTT_USE_TLS 1
#define CONFIG_UL_MQTT_REQUIRE_CLIENT_CERT 1
#define CONFIG_UL_MQTT_PROVISION_CERTS 1
#define CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN 1024
#define CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN 1024
#define CONFIG_UL_MQTT_TLS_SKIP_COMMON_NAME_CHECK 0
#define CONFIG_UL_MQTT_TLS_COMMON_NAME "test-broker"
#define CONFIG_UL_MQTT_CONNECT_TIMEOUT_MS 20000
#define CONFIG_UL_MQTT_RECONNECT_DELAY_MS 3000

#define ESP_EVENT_ANY_ID (-1)

typedef void *esp_event_base_t;
typedef void (*esp_event_handler_t)(void *handler_args, esp_event_base_t base,
                                    int32_t event_id, void *event_data);

typedef void (*esp_timer_cb_t)(void *);

typedef struct esp_timer {
  esp_timer_cb_t callback;
  void *arg;
  bool active;
  uint64_t timeout_us;
} esp_timer_t;

typedef esp_timer_t *esp_timer_handle_t;

typedef struct {
  esp_timer_cb_t callback;
  void *arg;
  const char *name;
} esp_timer_create_args_t;

esp_err_t esp_timer_create(const esp_timer_create_args_t *args,
                           esp_timer_handle_t *out_handle);
esp_err_t esp_timer_start_once(esp_timer_handle_t timer, uint64_t timeout_us);
esp_err_t esp_timer_stop(esp_timer_handle_t timer);

struct ul_mqtt_test_client;
typedef struct ul_mqtt_test_client *esp_mqtt_client_handle_t;

typedef enum {
  MQTT_TRANSPORT_OVER_TCP = 0,
  MQTT_TRANSPORT_OVER_SSL = 1,
  MQTT_TRANSPORT_OVER_WS = 2,
  MQTT_TRANSPORT_OVER_WSS = 3,
} mqtt_transport_t;

typedef mqtt_transport_t esp_mqtt_transport_t;

typedef struct {
  struct {
    struct {
      const char *uri;
      const char *hostname;
      int port;
      mqtt_transport_t transport;
    } address;
    struct {
      bool use_global_ca_store;
      void *crt_bundle_attach;
      const char *certificate;
      size_t certificate_len;
      const void *psk_hint_key;
      bool skip_cert_common_name_check;
      const char **alpn_protos;
      const char *common_name;
      const int *ciphersuites_list;
    } verification;
  } broker;
  struct {
    const char *username;
    struct {
      const char *password;
      const char *certificate;
      size_t certificate_len;
      const char *key;
      size_t key_len;
    } authentication;
  } credentials;
  struct {
    int priority;
    int stack_size;
  } task;
  struct {
    bool disable_auto_reconnect;
    int reconnect_timeout_ms;
    int timeout_ms;
  } network;
} esp_mqtt_client_config_t;

esp_mqtt_client_handle_t esp_mqtt_client_init(const esp_mqtt_client_config_t *cfg);
esp_err_t esp_mqtt_client_register_event(esp_mqtt_client_handle_t client,
                                         int32_t event_id,
                                         esp_event_handler_t handler,
                                         void *event_data);
esp_err_t esp_mqtt_client_start(esp_mqtt_client_handle_t client);
esp_err_t esp_mqtt_client_stop(esp_mqtt_client_handle_t client);
esp_err_t esp_mqtt_client_destroy(esp_mqtt_client_handle_t client);

typedef struct {
  char ssid[33];
  char password[65];
  char user[65];
  char user_password[129];
  char wifi_username[65];
  char wifi_user_password[129];
  unsigned char mqtt_client_cert[CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN];
  size_t mqtt_client_cert_len;
  unsigned char mqtt_client_key[CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN];
  size_t mqtt_client_key_len;
} ul_wifi_credentials_t;

extern bool g_stub_credentials_available;
extern ul_wifi_credentials_t g_stub_credentials;

static inline bool ul_wifi_credentials_load(ul_wifi_credentials_t *out) {
  if (!out)
    return false;
  *out = g_stub_credentials;
  return g_stub_credentials_available;
}

bool ul_core_is_connected(void);
void ul_health_notify_mqtt(bool connected);

typedef uint32_t TickType_t;

#define pdMS_TO_TICKS(ms) (ms)
void vTaskDelay(int ticks);

void motion_fade_cancel(void);
void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                        int32_t event_id, void *event_data);

esp_mqtt_client_handle_t ul_mqtt_test_get_client_handle(void);
bool ul_mqtt_test_retry_pending(void);
uint32_t ul_mqtt_test_consecutive_failures(void);
bool ul_mqtt_test_restart_pending(void);
const esp_mqtt_client_config_t *ul_mqtt_test_get_last_config(void);


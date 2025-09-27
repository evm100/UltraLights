#include "ul_provisioning.h"
#include "dns_server.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "ul_core.h"
#include "ul_wifi_credentials.h"
#include "esp_http_server.h"
#include "cJSON.h"
#include "esp_system.h"
#include "esp_mac.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PORTAL_EVENT_SUCCESS BIT0
#define PORTAL_EVENT_STOPPED BIT1

static const char *TAG = "ul_provision";

typedef enum {
  PROV_STATE_IDLE = 0,
  PROV_STATE_READY,
  PROV_STATE_CONNECTING,
  PROV_STATE_SUCCESS,
  PROV_STATE_FAILED,
} portal_state_t;

static ul_provisioning_config_t s_config;
static httpd_handle_t s_httpd;
static dns_server_handle_t *s_dns_handle;
static EventGroupHandle_t s_events;
static esp_timer_handle_t s_idle_timer;
static esp_netif_t *s_ap_netif;
static esp_netif_t *s_sta_netif;
static portal_state_t s_state = PROV_STATE_IDLE;
static portMUX_TYPE s_state_lock = portMUX_INITIALIZER_UNLOCKED;
static char s_status_ip[16];
static bool s_wifi_started;
static bool s_wifi_initialised;
static bool s_handlers_registered;

extern const uint8_t portal_index_html_start[] asm("_binary_portal_index_html_start");
extern const uint8_t portal_index_html_end[] asm("_binary_portal_index_html_end");

static const char *state_to_string(portal_state_t state) {
  switch (state) {
  case PROV_STATE_READY:
    return "ready";
  case PROV_STATE_CONNECTING:
    return "connecting";
  case PROV_STATE_SUCCESS:
    return "success";
  case PROV_STATE_FAILED:
    return "failed";
  default:
    return "idle";
  }
}

const char *ul_provisioning_get_state_string(void) {
  portal_state_t state;
  taskENTER_CRITICAL(&s_state_lock);
  state = s_state;
  taskEXIT_CRITICAL(&s_state_lock);
  return state_to_string(state);
}

static void set_state(portal_state_t state) {
  taskENTER_CRITICAL(&s_state_lock);
  s_state = state;
  if (state != PROV_STATE_SUCCESS) {
    s_status_ip[0] = '\0';
  }
  taskEXIT_CRITICAL(&s_state_lock);
}

static void set_state_success(const char *ip_str) {
  taskENTER_CRITICAL(&s_state_lock);
  s_state = PROV_STATE_SUCCESS;
  if (ip_str) {
    strlcpy(s_status_ip, ip_str, sizeof(s_status_ip));
  } else {
    s_status_ip[0] = '\0';
  }
  taskEXIT_CRITICAL(&s_state_lock);
}

static void reset_idle_timer(void) {
  if (!s_idle_timer || s_config.inactivity_timeout_ms == 0)
    return;
  esp_timer_stop(s_idle_timer);
  esp_timer_start_once(s_idle_timer, (uint64_t)s_config.inactivity_timeout_ms * 1000ULL);
}

static esp_err_t send_index_html(httpd_req_t *req) {
  reset_idle_timer();
  const size_t len = portal_index_html_end - portal_index_html_start;
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, (const char *)portal_index_html_start, len);
}

static void copy_username_lowercase(char *dest, size_t dest_size,
                                    const char *src) {
  if (!dest || dest_size == 0)
    return;
  dest[0] = '\0';
  if (!src)
    return;
  strlcpy(dest, src, dest_size);
  for (char *p = dest; *p; ++p) {
    *p = (char)tolower((unsigned char)*p);
  }
}

static void append_hotspot_headers(httpd_req_t *req) {
  httpd_resp_set_hdr(req, "Cache-Control", "no-cache, no-store, must-revalidate");
}

static esp_err_t root_handler(httpd_req_t *req) {
  append_hotspot_headers(req);
  return send_index_html(req);
}

static esp_err_t hotspot_probe_handler(httpd_req_t *req) {
  append_hotspot_headers(req);
  return send_index_html(req);
}

static esp_err_t status_handler(httpd_req_t *req) {
  reset_idle_timer();
  portal_state_t state;
  char ip_copy[sizeof(s_status_ip)];
  taskENTER_CRITICAL(&s_state_lock);
  state = s_state;
  strlcpy(ip_copy, s_status_ip, sizeof(ip_copy));
  taskEXIT_CRITICAL(&s_state_lock);

  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "state", state_to_string(state));
  if (state == PROV_STATE_SUCCESS && ip_copy[0] != '\0') {
    cJSON_AddStringToObject(root, "ip", ip_copy);
  }
  char *json = cJSON_PrintUnformatted(root);
  cJSON_Delete(root);
  if (!json)
    return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json error");
  httpd_resp_set_type(req, "application/json");
  esp_err_t res = httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
  cJSON_free(json);
  return res;
}

static esp_err_t scan_handler(httpd_req_t *req) {
  reset_idle_timer();
  wifi_scan_config_t scan_cfg = {
      .show_hidden = false,
      .scan_type = WIFI_SCAN_TYPE_ACTIVE,
  };
  esp_err_t err = esp_wifi_scan_start(&scan_cfg, true);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Scan failed: %s", esp_err_to_name(err));
    return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "scan failed");
  }

  uint16_t num = 0;
  esp_wifi_scan_get_ap_num(&num);
  if (num > 32)
    num = 32;
  wifi_ap_record_t *records = NULL;
  if (num > 0) {
    records = calloc(num, sizeof(*records));
    if (!records) {
      ESP_LOGE(TAG, "Failed to allocate scan record buffer");
      return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "alloc failed");
    }
  }
  wifi_ap_record_t dummy = {0};
  wifi_ap_record_t *record_buf = records ? records : &dummy;
  esp_err_t rec_err = esp_wifi_scan_get_ap_records(&num, record_buf);
  if (rec_err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to get scan results: %s", esp_err_to_name(rec_err));
    free(records);
    return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "scan failed");
  }

  cJSON *root = cJSON_CreateObject();
  cJSON *arr = cJSON_AddArrayToObject(root, "aps");
  for (uint16_t i = 0; i < num; ++i) {
    const wifi_ap_record_t *rec = &record_buf[i];
    cJSON *item = cJSON_CreateObject();
    char ssid[33];
    memcpy(ssid, rec->ssid, sizeof(rec->ssid));
    ssid[sizeof(ssid) - 1] = '\0';
    cJSON_AddStringToObject(item, "ssid", ssid);
    cJSON_AddNumberToObject(item, "rssi", rec->rssi);
    cJSON_AddItemToArray(arr, item);
  }
  char *json = cJSON_PrintUnformatted(root);
  cJSON_Delete(root);
  free(records);
  if (!json)
    return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "json error");
  httpd_resp_set_type(req, "application/json");
  esp_err_t res = httpd_resp_send(req, json, HTTPD_RESP_USE_STRLEN);
  cJSON_free(json);
  return res;
}

static bool parse_body(httpd_req_t *req, char *buffer, size_t buffer_len, size_t *out_len) {
  if (buffer_len == 0)
    return false;

  size_t remaining = req->content_len;
  size_t total = 0;
  while (remaining > 0) {
    size_t space = buffer_len - 1 - total;
    if (space == 0)
      return false;

    size_t to_read = remaining;
    if (to_read > space)
      to_read = space;

    int received = httpd_req_recv(req, buffer + total, to_read);
    if (received <= 0) {
      if (received == HTTPD_SOCK_ERR_TIMEOUT)
        continue;
      return false;
    }
    total += received;
    remaining -= received;
  }

  if (out_len)
    *out_len = total;
  buffer[total] = '\0';
  return true;
}

static void begin_connect(const char *ssid, const char *password) {
  wifi_config_t sta_cfg = {0};
  strlcpy((char *)sta_cfg.sta.ssid, ssid, sizeof(sta_cfg.sta.ssid));
  strlcpy((char *)sta_cfg.sta.password, password, sizeof(sta_cfg.sta.password));
  sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
  esp_wifi_disconnect();
  esp_err_t err = esp_wifi_set_mode(WIFI_MODE_APSTA);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to set APSTA mode: %s", esp_err_to_name(err));
  }
  err = esp_wifi_set_config(WIFI_IF_STA, &sta_cfg);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to set STA config: %s", esp_err_to_name(err));
  }
  err = esp_wifi_connect();
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "Failed to start STA connection: %s", esp_err_to_name(err));
  }
  set_state(PROV_STATE_CONNECTING);
}

static esp_err_t provision_handler(httpd_req_t *req) {
  reset_idle_timer();
  char body[512];
  size_t len = 0;
  if (!parse_body(req, body, sizeof(body), &len)) {
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid body");
  }
  cJSON *root = cJSON_Parse(body);
  if (!root) {
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid json");
  }
  const cJSON *ssid = cJSON_GetObjectItem(root, "ssid");
  const cJSON *account_password_json =
      cJSON_GetObjectItem(root, "account_password");
  const cJSON *username = cJSON_GetObjectItem(root, "username");
  const cJSON *wifi_password_json = cJSON_GetObjectItem(root, "password");
  if (!ssid || !cJSON_IsString(ssid) || ssid->valuestring[0] == '\0') {
    cJSON_Delete(root);
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing ssid");
  }
  const char *wifi_pass_str =
      (wifi_password_json && cJSON_IsString(wifi_password_json))
          ? wifi_password_json->valuestring
          : "";
  if (!username || !cJSON_IsString(username) || username->valuestring[0] == '\0') {
    cJSON_Delete(root);
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing username");
  }
  const char *account_password_str =
      (account_password_json && cJSON_IsString(account_password_json))
          ? account_password_json->valuestring
          : "";
  if (account_password_str[0] == '\0') {
    cJSON_Delete(root);
    return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing password");
  }

  ul_wifi_credentials_t creds = {0};
  strlcpy(creds.ssid, ssid->valuestring, sizeof(creds.ssid));
  strlcpy(creds.password, wifi_pass_str, sizeof(creds.password));
  copy_username_lowercase(creds.user, sizeof(creds.user),
                          username->valuestring);
  strlcpy(creds.user_password, account_password_str,
          sizeof(creds.user_password));
  esp_err_t err = ul_wifi_credentials_save(&creds);
  if (err != ESP_OK) {
    cJSON_Delete(root);
    return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "persist failed");
  }
  begin_connect(creds.ssid, creds.password);
  cJSON_Delete(root);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_sendstr(req, "{\"ok\":true}");
}

static void idle_timer_cb(void *arg) {
  (void)arg;
  ESP_LOGW(TAG, "Provisioning portal idle timeout reached, stopping portal");
  ul_provisioning_stop();
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id,
                               void *event_data) {
  (void)arg;
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    ESP_LOGW(TAG, "Station disconnected during provisioning");
    set_state(PROV_STATE_FAILED);
  }
}

static void ip_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id,
                             void *event_data) {
  (void)arg;
  if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
    char ip[16];
    esp_ip4addr_ntoa(&event->ip_info.ip, ip, sizeof(ip));
    ESP_LOGI(TAG, "Provisioned successfully, got IP %s", ip);
    set_state_success(ip);
    if (s_events) {
      xEventGroupSetBits(s_events, PORTAL_EVENT_SUCCESS);
    }
  }
}

void ul_provisioning_make_default_config(ul_provisioning_config_t *cfg) {
  if (!cfg)
    return;
  memset(cfg, 0, sizeof(*cfg));
  cfg->channel = 6;
  cfg->inactivity_timeout_ms = 10 * 60 * 1000;
  const char *node_id = ul_core_get_node_id();
  const char *suffix = "0000";
  if (node_id && strlen(node_id) >= 4) {
    suffix = node_id + strlen(node_id) - 4;
  }
  snprintf(cfg->ap_ssid, sizeof(cfg->ap_ssid), "UltraLights-%s", suffix);
  strlcpy(cfg->ap_password, "UltraLights", sizeof(cfg->ap_password));
}

esp_err_t ul_provisioning_start(const ul_provisioning_config_t *cfg) {
  if (!cfg)
    return ESP_ERR_INVALID_ARG;
  if (s_httpd)
    return ESP_ERR_INVALID_STATE;

  s_config = *cfg;
  set_state(PROV_STATE_READY);

  if (!s_events) {
    s_events = xEventGroupCreate();
    if (!s_events)
      return ESP_ERR_NO_MEM;
  } else {
    xEventGroupClearBits(s_events, PORTAL_EVENT_SUCCESS | PORTAL_EVENT_STOPPED);
  }

  if (cfg->inactivity_timeout_ms > 0) {
    if (!s_idle_timer) {
      const esp_timer_create_args_t timer_args = {
          .callback = idle_timer_cb,
          .name = "prov_idle",
      };
      esp_err_t timer_err = esp_timer_create(&timer_args, &s_idle_timer);
      if (timer_err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create idle timer: %s", esp_err_to_name(timer_err));
        return timer_err;
      }
    }
    reset_idle_timer();
  }

  wifi_init_config_t wifi_cfg = WIFI_INIT_CONFIG_DEFAULT();
  esp_err_t err = esp_wifi_init(&wifi_cfg);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_wifi_init failed: %s", esp_err_to_name(err));
    goto fail;
  }
  s_wifi_initialised = true;

  s_ap_netif = esp_netif_create_default_wifi_ap();
  s_sta_netif = esp_netif_create_default_wifi_sta();
  if (!s_ap_netif || !s_sta_netif) {
    ESP_LOGE(TAG, "Failed to create Wi-Fi netifs");
    err = ESP_FAIL;
    goto fail;
  }

  wifi_config_t ap_cfg = {0};
  strlcpy((char *)ap_cfg.ap.ssid, cfg->ap_ssid, sizeof(ap_cfg.ap.ssid));
  ap_cfg.ap.ssid_len = strlen(cfg->ap_ssid);
  strlcpy((char *)ap_cfg.ap.password, cfg->ap_password, sizeof(ap_cfg.ap.password));
  size_t pass_len = strlen(cfg->ap_password);
  bool has_valid_password = pass_len >= 8;
  if (pass_len > 0 && !has_valid_password) {
    ESP_LOGW(TAG, "SoftAP password length (%zu) below WPA2 minimum; starting open AP", pass_len);
    memset(ap_cfg.ap.password, 0, sizeof(ap_cfg.ap.password));
    pass_len = 0;
    has_valid_password = false;
    memset(s_config.ap_password, 0, sizeof(s_config.ap_password));
  }
  ap_cfg.ap.channel = cfg->channel ? cfg->channel : 6;
  ap_cfg.ap.max_connection = 4;
  ap_cfg.ap.authmode = has_valid_password ? WIFI_AUTH_WPA_WPA2_PSK : WIFI_AUTH_OPEN;
  ap_cfg.ap.pmf_cfg.capable = true;
  ap_cfg.ap.pmf_cfg.required = false;

  err = esp_wifi_set_mode(WIFI_MODE_APSTA);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set APSTA mode: %s", esp_err_to_name(err));
    goto fail;
  }
  err = esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to set AP config: %s", esp_err_to_name(err));
    goto fail;
  }
  err = esp_wifi_start();
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "esp_wifi_start failed: %s", esp_err_to_name(err));
    goto fail;
  }
  s_wifi_started = true;

  err = esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, wifi_event_handler, NULL);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to register Wi-Fi handler: %s", esp_err_to_name(err));
    goto fail;
  }
  err = esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, ip_event_handler, NULL);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to register IP handler: %s", esp_err_to_name(err));
    goto fail;
  }
  s_handlers_registered = true;

  esp_netif_ip_info_t ip_info;
  err = esp_netif_get_ip_info(s_ap_netif, &ip_info);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to get AP IP info: %s", esp_err_to_name(err));
    goto fail;
  }
  uint32_t ip_host = lwip_ntohl(ip_info.ip.addr);
  err = ul_dns_server_start(ip_host, &s_dns_handle);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "DNS server failed to start: %s", esp_err_to_name(err));
    s_dns_handle = NULL;
  }

  httpd_config_t http_cfg = HTTPD_DEFAULT_CONFIG();
  http_cfg.server_port = 80;
  http_cfg.stack_size = 8192;
  http_cfg.uri_match_fn = httpd_uri_match_wildcard;
  err = httpd_start(&s_httpd, &http_cfg);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to start HTTP server: %s", esp_err_to_name(err));
    goto fail;
  }

  httpd_uri_t root = {.uri = "/", .method = HTTP_GET, .handler = root_handler, .user_ctx = NULL};
  httpd_uri_t status = {.uri = "/api/status", .method = HTTP_GET, .handler = status_handler, .user_ctx = NULL};
  httpd_uri_t scan = {.uri = "/api/scan", .method = HTTP_GET, .handler = scan_handler, .user_ctx = NULL};
  httpd_uri_t provision = {.uri = "/api/provision", .method = HTTP_POST, .handler = provision_handler, .user_ctx = NULL};
  httpd_uri_t hotspot = {.uri = "/hotspot-detect.html", .method = HTTP_GET, .handler = hotspot_probe_handler, .user_ctx = NULL};
  httpd_uri_t generate204 = {.uri = "/generate_204", .method = HTTP_GET, .handler = hotspot_probe_handler, .user_ctx = NULL};
  httpd_uri_t captive = {.uri = "/*", .method = HTTP_GET, .handler = root_handler, .user_ctx = NULL};

  httpd_register_uri_handler(s_httpd, &root);
  httpd_register_uri_handler(s_httpd, &status);
  httpd_register_uri_handler(s_httpd, &scan);
  httpd_register_uri_handler(s_httpd, &provision);
  httpd_register_uri_handler(s_httpd, &hotspot);
  httpd_register_uri_handler(s_httpd, &generate204);
  httpd_register_uri_handler(s_httpd, &captive);

  const char *log_password = has_valid_password ? cfg->ap_password : "(open)";
  ESP_LOGI(TAG, "Provisioning portal running. AP SSID: %s (password: %s)", cfg->ap_ssid,
           log_password);
  return ESP_OK;

fail:
  ul_provisioning_stop();
  return err;
}

bool ul_provisioning_wait_for_completion(TickType_t ticks_to_wait, char *ip_buffer, size_t ip_buffer_len) {
  if (!s_events)
    return false;
  EventBits_t bits = xEventGroupWaitBits(s_events, PORTAL_EVENT_SUCCESS | PORTAL_EVENT_STOPPED,
                                         pdFALSE, pdFALSE, ticks_to_wait);
  if (bits & PORTAL_EVENT_SUCCESS) {
    if (ip_buffer && ip_buffer_len > 0) {
      taskENTER_CRITICAL(&s_state_lock);
      strlcpy(ip_buffer, s_status_ip, ip_buffer_len);
      taskEXIT_CRITICAL(&s_state_lock);
    }
    return true;
  }
  return false;
}

void ul_provisioning_stop(void) {
  if (s_idle_timer) {
    esp_timer_stop(s_idle_timer);
  }

  if (s_httpd) {
    httpd_stop(s_httpd);
    s_httpd = NULL;
  }

  if (s_dns_handle) {
    ul_dns_server_stop(s_dns_handle);
    s_dns_handle = NULL;
  }

  if (s_handlers_registered) {
    esp_event_handler_unregister(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, wifi_event_handler);
    esp_event_handler_unregister(IP_EVENT, IP_EVENT_STA_GOT_IP, ip_event_handler);
    s_handlers_registered = false;
  }

  if (s_wifi_started) {
    esp_wifi_stop();
    s_wifi_started = false;
  }
  if (s_wifi_initialised) {
    esp_wifi_deinit();
    s_wifi_initialised = false;
  }

  if (s_ap_netif) {
    esp_netif_destroy(s_ap_netif);
    s_ap_netif = NULL;
  }
  if (s_sta_netif) {
    esp_netif_destroy(s_sta_netif);
    s_sta_netif = NULL;
  }

  set_state(PROV_STATE_IDLE);

  if (s_events) {
    xEventGroupSetBits(s_events, PORTAL_EVENT_STOPPED);
  }
}

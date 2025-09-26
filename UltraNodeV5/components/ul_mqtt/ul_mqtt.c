#ifdef UL_MQTT_TESTING
#include "ul_mqtt_test_stubs.h"
#else
#include "ul_mqtt.h"
#include "cJSON.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#ifndef UL_MQTT_TESTING
#include "esp_crt_bundle.h"
#endif
#include "mqtt_client.h"
#include "sdkconfig.h"
#include "ul_core.h"
#include "ul_health.h"
#include "ul_state.h"
#include "ul_ota.h"
#include "ul_white_engine.h"
#include "ul_ws_engine.h"
#include "ul_rgb_engine.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"
#endif

#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static const char *TAG = "ul_mqtt";
static esp_mqtt_client_handle_t s_client = NULL;
static bool s_ready = false;

static esp_mqtt_transport_t transport_from_uri(const char *uri, bool tls_enabled) {
  if (!uri)
    return tls_enabled ? MQTT_TRANSPORT_OVER_SSL : MQTT_TRANSPORT_OVER_TCP;
  if (strncmp(uri, "mqtts://", strlen("mqtts://")) == 0)
    return MQTT_TRANSPORT_OVER_SSL;
  if (strncmp(uri, "mqtt://", strlen("mqtt://")) == 0)
    return MQTT_TRANSPORT_OVER_TCP;
  if (strncmp(uri, "wss://", strlen("wss://")) == 0)
    return MQTT_TRANSPORT_OVER_WSS;
  if (strncmp(uri, "ws://", strlen("ws://")) == 0)
    return tls_enabled ? MQTT_TRANSPORT_OVER_WSS : MQTT_TRANSPORT_OVER_WS;
  return tls_enabled ? MQTT_TRANSPORT_OVER_SSL : MQTT_TRANSPORT_OVER_TCP;
}

static const char *transport_name(esp_mqtt_transport_t transport) {
  switch (transport) {
  case MQTT_TRANSPORT_OVER_TCP:
    return "tcp";
  case MQTT_TRANSPORT_OVER_SSL:
    return "ssl";
  case MQTT_TRANSPORT_OVER_WS:
    return "ws";
  case MQTT_TRANSPORT_OVER_WSS:
    return "wss";
  default:
    return "unknown";
  }
}

static bool uri_authority_range(const char *uri, const char **authority_out,
                                const char **end_out) {
  if (!uri || !authority_out || !end_out)
    return false;

  const char *authority = uri;
  const char *scheme_end = strstr(uri, "://");
  if (scheme_end)
    authority = scheme_end + 3;
  if (!authority || *authority == '\0')
    return false;

  const char *path = strchr(authority, '/');
  const char *end = path ? path : authority + strlen(authority);
  if (authority == end)
    return false;

  *authority_out = authority;
  *end_out = end;
  return true;
}

static bool parse_host_from_uri(const char *uri, char *out, size_t out_len) {
  if (!uri || !out || out_len == 0)
    return false;

  const char *authority = NULL;
  const char *end = NULL;
  if (!uri_authority_range(uri, &authority, &end))
    return false;

  if (*authority == '[') {
    const char *closing = memchr(authority, ']', end - authority);
    if (!closing)
      return false;
    size_t len = closing - authority - 1;
    if (len + 1 > out_len)
      len = out_len - 1;
    memcpy(out, authority + 1, len);
    out[len] = '\0';
    return true;
  }

  const char *colon = memchr(authority, ':', end - authority);
  if (colon)
    end = colon;

  size_t len = end - authority;
  if (len == 0)
    return false;
  if (len + 1 > out_len)
    len = out_len - 1;
  memcpy(out, authority, len);
  out[len] = '\0';
  return true;
}

static int parse_port_from_uri(const char *uri, int default_port) {
  if (!uri)
    return default_port;

  const char *authority = NULL;
  const char *end = NULL;
  if (!uri_authority_range(uri, &authority, &end))
    return default_port;
  const char *colon = NULL;
  if (authority < end && authority[0] == '[') {
    const char *closing = memchr(authority, ']', end - authority);
    if (closing && closing + 1 < end && closing[1] == ':') {
      colon = closing + 1;
    }
  } else {
    colon = memchr(authority, ':', end - authority);
  }
  if (!colon)
    return default_port;
  int port = atoi(colon + 1);
  if (port <= 0 || port > 65535)
    return default_port;
  return port;
}

#ifndef UL_MQTT_TESTING
static EventGroupHandle_t s_state_event_group;
static portMUX_TYPE s_state_event_group_lock = portMUX_INITIALIZER_UNLOCKED;
#define UL_MQTT_READY_BIT BIT0

static EventGroupHandle_t mqtt_state_event_group(void) {
  portENTER_CRITICAL(&s_state_event_group_lock);
  EventGroupHandle_t group = s_state_event_group;
  if (!group) {
    group = xEventGroupCreate();
    if (group)
      s_state_event_group = group;
  }
  portEXIT_CRITICAL(&s_state_event_group_lock);
  if (!group)
    ESP_LOGE(TAG, "Failed to create MQTT state event group");
  return group;
}
#endif

#ifndef UL_MQTT_TESTING
#define UL_MQTT_PUBLISH_ACK_QUEUE_LENGTH 8
#define UL_MQTT_PUBLISH_ACK_TIMEOUT_MS 2000
static QueueHandle_t s_publish_ack_queue = NULL;
#endif

#define UL_MQTT_RETRY_DELAY_US (5ULL * 1000000ULL)

static esp_timer_handle_t s_retry_timer = NULL;
static bool s_retry_pending = false;

#ifndef UL_MQTT_TESTING

#define UL_WS_MAX_STRIPS 2
#define UL_RGB_MAX_STRIPS 4
#define UL_WHITE_MAX_CHANNELS 4

static uint8_t s_ws_saved_bri[UL_WS_MAX_STRIPS];
static bool s_ws_saved_valid[UL_WS_MAX_STRIPS];
static uint8_t s_rgb_saved_bri[UL_RGB_MAX_STRIPS];
static bool s_rgb_saved_valid[UL_RGB_MAX_STRIPS];
static uint8_t s_white_saved_bri[UL_WHITE_MAX_CHANNELS];
static bool s_white_saved_valid[UL_WHITE_MAX_CHANNELS];
static bool s_lights_dimmed = false;

typedef struct {
  bool active;
  int total_steps;
  int current_step;
  uint64_t interval_us;
  uint8_t ws_initial_bri[UL_WS_MAX_STRIPS];
  bool ws_active[UL_WS_MAX_STRIPS];
  uint8_t rgb_initial_bri[UL_RGB_MAX_STRIPS];
  bool rgb_active[UL_RGB_MAX_STRIPS];
  uint8_t white_initial_bri[UL_WHITE_MAX_CHANNELS];
  bool white_active[UL_WHITE_MAX_CHANNELS];
} motion_fade_state_t;

static motion_fade_state_t s_motion_fade = {0};
static esp_timer_handle_t s_motion_fade_timer = NULL;

static bool pir_task_compiled(void) {
#if defined(CONFIG_UL_PIR_ENABLED) && CONFIG_UL_PIR_ENABLED
  return true;
#else
  return false;
#endif
}

static void remember_ws_brightness(void) {
  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    ul_ws_strip_status_t st;
    if (ul_ws_get_status(i, &st) && st.enabled) {
      s_ws_saved_bri[i] = st.brightness;
      s_ws_saved_valid[i] = true;
    } else {
      s_ws_saved_valid[i] = false;
    }
  }
}

static void remember_rgb_brightness(void) {
  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    ul_rgb_strip_status_t st;
    if (ul_rgb_get_status(i, &st) && st.enabled) {
      s_rgb_saved_bri[i] = st.brightness;
      s_rgb_saved_valid[i] = true;
    } else {
      s_rgb_saved_valid[i] = false;
    }
  }
}

static void remember_white_brightness(void) {
  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    ul_white_ch_status_t st;
    if (ul_white_get_status(i, &st) && st.enabled) {
      s_white_saved_bri[i] = st.brightness;
      s_white_saved_valid[i] = true;
    } else {
      s_white_saved_valid[i] = false;
    }
  }
}

static void dim_all_lights(void) {
  if (s_lights_dimmed)
    return;

  remember_ws_brightness();
  remember_rgb_brightness();
  remember_white_brightness();

  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    if (s_ws_saved_valid[i]) {
      ul_ws_set_brightness(i, 0);
    }
  }

  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    if (s_rgb_saved_valid[i]) {
      ul_rgb_set_brightness(i, 0);
    }
  }

  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    if (s_white_saved_valid[i]) {
      ul_white_set_brightness(i, 0);
    }
  }

  s_lights_dimmed = true;
}

static void restore_all_lights(void) {
  if (!s_lights_dimmed)
    return;

  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    if (s_ws_saved_valid[i]) {
      ul_ws_set_brightness(i, s_ws_saved_bri[i]);
      s_ws_saved_valid[i] = false;
    }
  }

  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    if (s_rgb_saved_valid[i]) {
      ul_rgb_set_brightness(i, s_rgb_saved_bri[i]);
      s_rgb_saved_valid[i] = false;
    }
  }

  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    if (s_white_saved_valid[i]) {
      ul_white_set_brightness(i, s_white_saved_bri[i]);
      s_white_saved_valid[i] = false;
    }
  }

  s_lights_dimmed = false;
}

static void motion_fade_apply_level(int step);

static bool motion_fade_snapshot_channels(void) {
  bool any = false;

  memset(s_motion_fade.ws_active, 0, sizeof(s_motion_fade.ws_active));
  memset(s_motion_fade.rgb_active, 0, sizeof(s_motion_fade.rgb_active));
  memset(s_motion_fade.white_active, 0, sizeof(s_motion_fade.white_active));

  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    ul_ws_strip_status_t st;
    if (ul_ws_get_status(i, &st) && st.enabled && st.brightness > 0) {
      s_motion_fade.ws_initial_bri[i] = st.brightness;
      s_motion_fade.ws_active[i] = true;
      any = true;
    }
  }

  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    ul_rgb_strip_status_t st;
    if (ul_rgb_get_status(i, &st) && st.enabled && st.brightness > 0) {
      s_motion_fade.rgb_initial_bri[i] = st.brightness;
      s_motion_fade.rgb_active[i] = true;
      any = true;
    }
  }

  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    ul_white_ch_status_t st;
    if (ul_white_get_status(i, &st) && st.enabled && st.brightness > 0) {
      s_motion_fade.white_initial_bri[i] = st.brightness;
      s_motion_fade.white_active[i] = true;
      any = true;
    }
  }

  return any;
}

static void motion_fade_stop_timer(void) {
  if (s_motion_fade_timer) {
    esp_timer_stop(s_motion_fade_timer);
  }
}

static void motion_fade_cancel(void) {
  motion_fade_stop_timer();
  s_motion_fade.active = false;
  s_motion_fade.total_steps = 0;
  s_motion_fade.current_step = 0;
  s_motion_fade.interval_us = 0;
}

static void motion_fade_apply_level(int step) {
  int steps = s_motion_fade.total_steps;
  if (steps <= 0)
    steps = 1;

  int remaining = steps - step;
  if (remaining < 0)
    remaining = 0;

  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    if (!s_motion_fade.ws_active[i])
      continue;
    int start = s_motion_fade.ws_initial_bri[i];
    int value = 0;
    if (remaining > 0)
      value = (start * remaining + steps - 1) / steps;
    ul_ws_set_brightness(i, (uint8_t)value);
  }

  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    if (!s_motion_fade.rgb_active[i])
      continue;
    int start = s_motion_fade.rgb_initial_bri[i];
    int value = 0;
    if (remaining > 0)
      value = (start * remaining + steps - 1) / steps;
    ul_rgb_set_brightness(i, (uint8_t)value);
  }

  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    if (!s_motion_fade.white_active[i])
      continue;
    int start = s_motion_fade.white_initial_bri[i];
    int value = 0;
    if (remaining > 0)
      value = (start * remaining + steps - 1) / steps;
    ul_white_set_brightness(i, (uint8_t)value);
  }
}

static void motion_fade_timer_cb(void *arg) {
  if (!s_motion_fade.active)
    return;

  s_motion_fade.current_step++;
  motion_fade_apply_level(s_motion_fade.current_step);

  if (s_motion_fade.current_step >= s_motion_fade.total_steps) {
    motion_fade_cancel();
  }
}

static void motion_fade_start(int duration_ms, int steps) {
  motion_fade_cancel();

  if (!motion_fade_snapshot_channels())
    return;

  if (steps <= 0)
    steps = 8;
  if (duration_ms <= 0)
    duration_ms = 2000;

  uint64_t interval_us = ((uint64_t)duration_ms * 1000ULL) / steps;
  if (interval_us == 0)
    interval_us = 1000;

  s_motion_fade.total_steps = steps;
  s_motion_fade.current_step = 0;
  s_motion_fade.interval_us = interval_us;
  s_motion_fade.active = true;

  motion_fade_apply_level(0);

  if (!s_motion_fade_timer) {
    const esp_timer_create_args_t args = {
        .callback = &motion_fade_timer_cb,
        .name = "motion_fade",
    };
    esp_err_t err = esp_timer_create(&args, &s_motion_fade_timer);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "Failed to create motion fade timer: %s", esp_err_to_name(err));
      s_motion_fade.active = false;
      return;
    }
  }

  esp_err_t start_err = esp_timer_start_periodic(s_motion_fade_timer, interval_us);
  if (start_err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to start motion fade timer: %s", esp_err_to_name(start_err));
    s_motion_fade.active = false;
    return;
  }
}

static void motion_fade_immediate_off(void) {
  motion_fade_cancel();
  if (!motion_fade_snapshot_channels())
    return;

  for (int i = 0; i < UL_WS_MAX_STRIPS; ++i) {
    if (s_motion_fade.ws_active[i])
      ul_ws_set_brightness(i, 0);
  }
  for (int i = 0; i < UL_RGB_MAX_STRIPS; ++i) {
    if (s_motion_fade.rgb_active[i])
      ul_rgb_set_brightness(i, 0);
  }
  for (int i = 0; i < UL_WHITE_MAX_CHANNELS; ++i) {
    if (s_motion_fade.white_active[i])
      ul_white_set_brightness(i, 0);
  }
}

// JSON helpers (defined later)

static int starts_with(const char *s, const char *pfx) {
  return strncmp(s, pfx, strlen(pfx)) == 0;
}

// If the topic path encodes an integer index after the given prefix,
// overwrite or insert that field into the JSON payload.
static void override_index_from_path(cJSON *root, const char *sub,
                                     const char *prefix, const char *field) {
  const char *suffix = sub + strlen(prefix);
  if (!root || suffix[0] != '/')
    return;
  char *end;
  long v = strtol(suffix + 1, &end, 10);
  if (end <= suffix + 1)
    return; // no digits found
  cJSON *j = cJSON_GetObjectItem(root, field);
  if (!j) {
    cJSON_AddNumberToObject(root, field, (int)v);
  } else {
    j->valueint = (int)v;
    j->valuedouble = (double)v;
  }
}

// Helper to publish JSON
static int publish_json(const char *topic, const char *json) {

  if (!s_client || !ul_core_is_connected() || !json)
    return -1;
  return esp_mqtt_client_publish(s_client, topic, json, 0, 1, 0);
}

#ifndef UL_MQTT_TESTING
static bool wait_for_publish_ack(int msg_id, uint32_t timeout_ms) {
  if (msg_id <= 0)
    return false;
  if (!s_publish_ack_queue) {
    s_publish_ack_queue =
        xQueueCreate(UL_MQTT_PUBLISH_ACK_QUEUE_LENGTH, sizeof(int));
    if (!s_publish_ack_queue)
      return false;
  }

  TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
  if (timeout_ticks == 0)
    timeout_ticks = 1;
  TickType_t deadline = xTaskGetTickCount() + timeout_ticks;

  while (true) {
    TickType_t now = xTaskGetTickCount();
    TickType_t remaining = (deadline > now) ? (deadline - now) : 0;
    int ack_id = 0;
    if (xQueueReceive(s_publish_ack_queue, &ack_id, remaining) != pdTRUE)
      return false;
    if (ack_id == msg_id)
      return true;
  }
}
#endif

static cJSON *load_params_from_state(bool (*copy_fn)(int, char *, size_t),
                                     int index, char *buffer,
                                     size_t buffer_len) {
  if (!buffer || buffer_len == 0)
    return cJSON_CreateArray();

  cJSON *dup = NULL;
  if (copy_fn && copy_fn(index, buffer, buffer_len)) {
    cJSON *saved = cJSON_Parse(buffer);
    if (saved) {
      cJSON *params = cJSON_GetObjectItem(saved, "params");
      if (params && cJSON_IsArray(params)) {
        dup = cJSON_Duplicate(params, true);
      }
      cJSON_Delete(saved);
    }
  }
  if (!dup) {
    dup = cJSON_CreateArray();
  }
  return dup;
}

// Build and publish status JSON snapshot
static void publish_status_snapshot(void) {
  char topic[128];
  char saved_payload[UL_STATE_MAX_JSON_LEN];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "event", "snapshot");
  cJSON_AddStringToObject(root, "node", ul_core_get_node_id());
  cJSON_AddBoolToObject(root, "pir_enabled", pir_task_compiled());

  // uptime
  cJSON_AddNumberToObject(root, "uptime_s", esp_timer_get_time() / 1000000);

  // WS strips
  cJSON *jws = cJSON_CreateArray();
  for (int i = 0; i < 2; i++) {
    ul_ws_strip_status_t st;
    if (ul_ws_get_status(i, &st)) {
      cJSON *o = cJSON_CreateObject();
      cJSON_AddNumberToObject(o, "strip", i);
      cJSON_AddBoolToObject(o, "enabled", st.enabled);
      cJSON_AddStringToObject(o, "effect", st.effect);
      cJSON_AddNumberToObject(o, "brightness", st.brightness);
      cJSON *params_array =
          load_params_from_state(ul_state_copy_ws, i, saved_payload,
                                 sizeof(saved_payload));
      if (params_array)
        cJSON_AddItemToObject(o, "params", params_array);
      cJSON_AddNumberToObject(o, "pixels", st.pixels);
      cJSON_AddNumberToObject(o, "gpio", st.gpio);
      cJSON_AddNumberToObject(o, "fps", st.fps);
      cJSON *col = cJSON_CreateIntArray(
          (int[]){st.color[0], st.color[1], st.color[2]}, 3);
      cJSON_AddItemToObject(o, "color", col);
      cJSON_AddItemToArray(jws, o);
    }
  }
  cJSON_AddItemToObject(root, "ws", jws);

  // RGB strips
  cJSON *jrgb = cJSON_CreateArray();
  for (int i = 0; i < 4; i++) {
    ul_rgb_strip_status_t st;
    if (ul_rgb_get_status(i, &st)) {
      cJSON *o = cJSON_CreateObject();
      cJSON_AddNumberToObject(o, "strip", i);
      cJSON_AddBoolToObject(o, "enabled", st.enabled);
      cJSON_AddStringToObject(o, "effect", st.effect);
      cJSON_AddNumberToObject(o, "brightness", st.brightness);
      cJSON *params_array =
          load_params_from_state(ul_state_copy_rgb, i, saved_payload,
                                 sizeof(saved_payload));
      if (params_array)
        cJSON_AddItemToObject(o, "params", params_array);
      cJSON_AddNumberToObject(o, "pwm_hz", st.pwm_hz);
      cJSON *channels = cJSON_CreateArray();
      for (int c = 0; c < 3; ++c) {
        cJSON *ch = cJSON_CreateObject();
        cJSON_AddNumberToObject(ch, "gpio", st.channel[c].gpio);
        cJSON_AddNumberToObject(ch, "ledc_ch", st.channel[c].ledc_ch);
        cJSON_AddNumberToObject(ch, "mode", st.channel[c].ledc_mode);
        cJSON_AddItemToArray(channels, ch);
      }
      cJSON_AddItemToObject(o, "channels", channels);
      int color[3] = {st.color[0], st.color[1], st.color[2]};
      cJSON_AddItemToObject(o, "color", cJSON_CreateIntArray(color, 3));
      cJSON_AddItemToArray(jrgb, o);
    }
  }
  cJSON_AddItemToObject(root, "rgb", jrgb);

  // White channels
  cJSON *jw = cJSON_CreateArray();
  for (int i = 0; i < 4; i++) {
    ul_white_ch_status_t st;
    if (ul_white_get_status(i, &st)) {
      cJSON *o = cJSON_CreateObject();
      cJSON_AddNumberToObject(o, "channel", i);
      cJSON_AddBoolToObject(o, "enabled", st.enabled);
      cJSON_AddStringToObject(o, "effect", st.effect);
      cJSON_AddNumberToObject(o, "brightness", st.brightness);
      cJSON *params_array =
          load_params_from_state(ul_state_copy_white, i, saved_payload,
                                 sizeof(saved_payload));
      if (params_array)
        cJSON_AddItemToObject(o, "params", params_array);
      cJSON_AddNumberToObject(o, "gpio", st.gpio);
      cJSON_AddNumberToObject(o, "pwm_hz", st.pwm_hz);
      cJSON_AddItemToArray(jw, o);
    }
  }
  cJSON_AddItemToObject(root, "white", jw);

  // *Debugging only- download_id is secret
  // OTA (static fields from Kconfig)
  //cJSON *jota = cJSON_CreateObject();
  //cJSON_AddStringToObject(jota, "manifest_url", CONFIG_UL_OTA_MANIFEST_URL);
  //cJSON_AddItemToObject(root, "ota", jota);

  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

void ul_mqtt_publish_status(void) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  if (!root)
    return;
  cJSON_AddStringToObject(root, "status", "ok");

  wifi_ap_record_t ap_info = {0};
  if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
    cJSON_AddNumberToObject(root, "signal_dbi", ap_info.rssi);
  }

  char *json = cJSON_PrintUnformatted(root);
  if (json) {
    publish_json(topic, json);
    cJSON_free(json);
  }
  cJSON_Delete(root);
}

// Publish confirmation for ws/set including echo of effect parameters
static void publish_ws_ack(int strip, const char *effect, cJSON *params,
                           bool ok) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "event", "ack");
  if (ok) {
    cJSON_AddStringToObject(root, "status", "ok");
    cJSON_AddNumberToObject(root, "strip", strip);
    if (effect)
      cJSON_AddStringToObject(root, "effect", effect);
    if (params && cJSON_IsArray(params)) {
      cJSON_AddItemToObject(root, "params", cJSON_Duplicate(params, true));
    } else {
      cJSON_AddItemToObject(root, "params", cJSON_CreateArray());
    }
  } else {
    cJSON_AddStringToObject(root, "status", "error");
    cJSON_AddStringToObject(root, "error", "invalid effect");
  }
  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

static void publish_rgb_ack(int strip, const char *effect, cJSON *params,
                            int brightness, bool ok) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "event", "ack");
  if (ok) {
    cJSON_AddStringToObject(root, "status", "ok");
    cJSON_AddNumberToObject(root, "strip", strip);
    cJSON_AddNumberToObject(root, "brightness", brightness);
    if (effect)
      cJSON_AddStringToObject(root, "effect", effect);
    if (params && cJSON_IsArray(params)) {
      cJSON_AddItemToObject(root, "params", cJSON_Duplicate(params, true));
    } else {
      cJSON_AddItemToObject(root, "params", cJSON_CreateArray());
    }
  } else {
    cJSON_AddStringToObject(root, "status", "error");
    cJSON_AddStringToObject(root, "error", "invalid effect");
  }
  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

static void publish_white_ack(int channel, const char *effect, cJSON *params,
                              int brightness, bool ok) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "event", "ack");
  if (ok) {
    cJSON_AddStringToObject(root, "status", "ok");
    cJSON_AddNumberToObject(root, "channel", channel);
    cJSON_AddNumberToObject(root, "brightness", brightness);
    if (effect)
      cJSON_AddStringToObject(root, "effect", effect);
    if (params && cJSON_IsArray(params)) {
      cJSON_AddItemToObject(root, "params", cJSON_Duplicate(params, true));
    } else {
      cJSON_AddItemToObject(root, "params", cJSON_CreateArray());
    }
  } else {
    cJSON_AddStringToObject(root, "status", "error");
    cJSON_AddStringToObject(root, "error", "invalid effect");
  }
  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

void ul_mqtt_publish_motion(const char *sensor, const char *state) {
  char topic[128];
  char payload[64];
  snprintf(topic, sizeof(topic), "ul/%s/evt/%s/motion", ul_core_get_node_id(), sensor);
  snprintf(payload, sizeof(payload), "{\"state\":\"%s\"}", state);
  publish_json(topic, payload);
}

void ul_mqtt_publish_ota_event(const char *status, const char *detail) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/ota", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "status", status);
  if (detail)
    cJSON_AddStringToObject(root, "detail", detail);
  int msg_id = -1;
  char *json = cJSON_PrintUnformatted(root);
  if (json) {
    msg_id = publish_json(topic, json);
    cJSON_free(json);
  }
  cJSON_Delete(root);
#ifndef UL_MQTT_TESTING
  if (msg_id >= 0 && status && strcmp(status, "success") == 0) {
    if (!wait_for_publish_ack(msg_id, UL_MQTT_PUBLISH_ACK_TIMEOUT_MS)) {
      ESP_LOGW(TAG,
               "Timed out waiting for OTA success publish acknowledgment (msg_id=%d)",
               msg_id);
    }
  }
#endif
}

static void publish_motion_status(void) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/motion/status",
           ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddBoolToObject(root, "pir_enabled", pir_task_compiled());
  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

static bool handle_cmd_ws_set(cJSON *root, int *out_strip) {
  int strip = 0;
  cJSON *jstrip = cJSON_GetObjectItem(root, "strip");
  if (jstrip && cJSON_IsNumber(jstrip))
    strip = jstrip->valueint;

  if (out_strip)
    *out_strip = strip;

  const char *effect = NULL;
  cJSON *jeffect = cJSON_GetObjectItem(root, "effect");
  if (jeffect && cJSON_IsString(jeffect))
    effect = jeffect->valuestring;

  cJSON *params = cJSON_GetObjectItem(root, "params");

  ul_ws_apply_json(root);

  bool ok = false;
  if (effect) {
    ul_ws_strip_status_t st;
    if (ul_ws_get_status(strip, &st)) {
      ok = strcmp(st.effect, effect) == 0;
    }
  }

  publish_ws_ack(strip, effect, params, ok);
  return (!effect || ok);
}

static bool handle_cmd_rgb_set(cJSON *root, int *out_strip) {
  int strip = 0;
  cJSON *jstrip = cJSON_GetObjectItem(root, "strip");
  if (jstrip && cJSON_IsNumber(jstrip))
    strip = jstrip->valueint;

  if (out_strip)
    *out_strip = strip;

  int brightness = 255;
  cJSON *jbri = cJSON_GetObjectItem(root, "brightness");
  if (jbri && cJSON_IsNumber(jbri))
    brightness = jbri->valueint;

  const char *effect = NULL;
  cJSON *jeffect = cJSON_GetObjectItem(root, "effect");
  if (jeffect && cJSON_IsString(jeffect))
    effect = jeffect->valuestring;

  cJSON *params = cJSON_GetObjectItem(root, "params");

  ul_rgb_apply_json(root);

  bool ok = false;
  if (effect) {
    ul_rgb_strip_status_t st;
    if (ul_rgb_get_status(strip, &st)) {
      ok = strcmp(st.effect, effect) == 0;
    }
  }

  publish_rgb_ack(strip, effect, params, brightness, ok);
  return (!effect || ok);
}

static bool handle_cmd_white_set(cJSON *root, int *out_channel) {
  int channel = 0;
  cJSON *jch = cJSON_GetObjectItem(root, "channel");
  if (jch && cJSON_IsNumber(jch))
    channel = jch->valueint;

  if (out_channel)
    *out_channel = channel;

  int brightness = 255;
  cJSON *jbri = cJSON_GetObjectItem(root, "brightness");
  if (jbri && cJSON_IsNumber(jbri))
    brightness = jbri->valueint;

  const char *effect = NULL;
  cJSON *jeffect = cJSON_GetObjectItem(root, "effect");
  if (jeffect && cJSON_IsString(jeffect))
    effect = jeffect->valuestring;

  cJSON *params = cJSON_GetObjectItem(root, "params");

  ul_white_apply_json(root);

  ul_white_ch_status_t st;
  bool have_status = ul_white_get_status(channel, &st);
  if (have_status)
    brightness = st.brightness;

  bool ok = have_status;
  if (effect && have_status)
    ok = strcmp(st.effect, effect) == 0;

  publish_white_ack(channel, effect, params, brightness, ok);

  return (!effect || ok);
}
static void on_message(esp_mqtt_event_handle_t event) {
  // topic expected: ul/<node>/cmd/...
  char node[64] = {0};
  const char *topic = event->topic;
  int tlen = event->topic_len;
  if (!topic || tlen <= 0)
    return;

  // Extract node id segment
  // pattern: "ul/xxxx/cmd/..."
  const char *p = memchr(topic, '/', tlen);
  if (!p)
    return;
  p++; // after "ul/"
  const char *slash2 = memchr(p, '/', (topic + tlen) - p);
  if (!slash2)
    return;
  int node_len = (int)(slash2 - p);
  if (node_len <= 0 || node_len >= (int)sizeof(node))
    return;
  memcpy(node, p, node_len);
  node[node_len] = 0;

  if (strcmp(node, ul_core_get_node_id()) != 0 && strcmp(node, "+") != 0) {
    // not for us
    return;
  }

  // Grab command path after "ul/<node>/"
  const char *cmdroot = slash2 + 1;
  int cmdlen = (topic + tlen) - cmdroot;

  // Parse JSON
  cJSON *root = cJSON_ParseWithLength(event->data, event->data_len);
  if (!root) {
    ESP_LOGW(TAG, "Invalid JSON payload");
    return;
  }

  if (cmdlen >= 3 && strncmp(cmdroot, "cmd", 3) == 0) {
    const char *sub = cmdroot + 4; // skip "cmd/"
    if (starts_with(sub, "ws/set")) {
      motion_fade_cancel();
      override_index_from_path(root, sub, "ws/set", "strip");
      int strip = 0;
      bool applied = handle_cmd_ws_set(root, &strip);
      if (applied) {
        if (event->data && event->data_len > 0) {
          ul_state_record_ws(strip, event->data, event->data_len);
        }
      }
    } else if (starts_with(sub, "rgb/set")) {
      motion_fade_cancel();
      override_index_from_path(root, sub, "rgb/set", "strip");
      int strip = 0;
      bool applied = handle_cmd_rgb_set(root, &strip);
      if (applied) {
        if (event->data && event->data_len > 0) {
          ul_state_record_rgb(strip, event->data, event->data_len);
        }
      }
    } else if (starts_with(sub, "ota/check")) {
      ul_mqtt_publish_status();
      ul_ota_check_now(true);
      publish_status_snapshot();
    }
    else if (starts_with(sub, "white/set")) {
      motion_fade_cancel();
      override_index_from_path(root, sub, "white/set", "channel");
      int channel = 0;
      bool applied = handle_cmd_white_set(root, &channel);
      if (applied) {
        if (event->data && event->data_len > 0) {
          ul_state_record_white(channel, event->data, event->data_len);
        }
      }
    } else if (starts_with(sub, "motion/off")) {
      int duration_ms = 2000;
      int steps = 255;
      cJSON *fade = cJSON_GetObjectItem(root, "fade");
      if (fade && cJSON_IsObject(fade)) {
        cJSON *dur = cJSON_GetObjectItem(fade, "duration_ms");
        if (dur && cJSON_IsNumber(dur))
          duration_ms = dur->valueint;
        cJSON *jsteps = cJSON_GetObjectItem(fade, "steps");
        if (jsteps && cJSON_IsNumber(jsteps))
          steps = jsteps->valueint;
      }
      if (duration_ms <= 0 || steps <= 0) {
        motion_fade_immediate_off();
      } else {
        motion_fade_start(duration_ms, steps);
      }
    } else if (starts_with(sub, "motion/on")) {
      motion_fade_cancel();
    } else if (starts_with(sub, "motion/status")) {
      publish_motion_status();
    } else if (starts_with(sub, "status")) {
      ul_mqtt_publish_status_now();
    } else {
      ESP_LOGW(TAG, "Unknown cmd path: %.*s", cmdlen, cmdroot);
    }
  }
  cJSON_Delete(root);
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base,
                               int32_t event_id, void *event_data) {
  esp_mqtt_event_handle_t event = event_data;
  switch (event->event_id) {
  case MQTT_EVENT_CONNECTED: {
    ESP_LOGI(TAG, "MQTT connected");
    s_ready = true;
#ifndef UL_MQTT_TESTING
    EventGroupHandle_t group = mqtt_state_event_group();
    if (group)
      xEventGroupSetBits(group, UL_MQTT_READY_BIT);
    if (s_publish_ack_queue)
      xQueueReset(s_publish_ack_queue);
#endif
    ul_health_notify_mqtt(true);
    restore_all_lights();
    if (ul_core_is_connected()) {
      char topic[128];
      snprintf(topic, sizeof(topic), "ul/%s/cmd/#", ul_core_get_node_id());
      esp_mqtt_client_subscribe(s_client, topic, 1);
      // Also allow broadcast to any node if you publish to ul/+/cmd/#
      esp_mqtt_client_subscribe(s_client, "ul/+/cmd/#", 0);
    }
    break;
  }
#ifndef UL_MQTT_TESTING
  case MQTT_EVENT_PUBLISHED: {
    ESP_LOGD(TAG, "MQTT published msg_id=%d", event->msg_id);
    if (s_publish_ack_queue) {
      int msg_id = event->msg_id;
      if (xQueueSend(s_publish_ack_queue, &msg_id, 0) != pdTRUE) {
        int dropped;
        if (xQueueReceive(s_publish_ack_queue, &dropped, 0) == pdTRUE) {
          if (xQueueSend(s_publish_ack_queue, &msg_id, 0) != pdTRUE) {
            ESP_LOGW(TAG, "Failed to enqueue publish acknowledgment (msg_id=%d)",
                     msg_id);
          }
        } else {
          ESP_LOGW(TAG, "Failed to enqueue publish acknowledgment (msg_id=%d)",
                   msg_id);
        }
      }
    }
    break;
  }
#endif
  case MQTT_EVENT_DISCONNECTED:
    ESP_LOGW(TAG, "MQTT disconnected");
    s_ready = false;
    ul_health_notify_mqtt(false);
    dim_all_lights();
#ifndef UL_MQTT_TESTING
    EventGroupHandle_t group = mqtt_state_event_group();
    if (group)
      xEventGroupClearBits(group, UL_MQTT_READY_BIT);
    if (s_publish_ack_queue)
      xQueueReset(s_publish_ack_queue);
#endif
    break;
  case MQTT_EVENT_DATA:
    on_message(event);
    break;
  case MQTT_EVENT_ERROR: {
    esp_mqtt_error_codes_t *err = event->error_handle;
    if (err) {
      ESP_LOGE(TAG,
               "MQTT error: type=%d socket_errno=%d tls_err=0x%x"
               " tls_cert_flags=0x%x conn_return=%d",
               err->error_type, err->esp_transport_sock_errno,
               (unsigned int)err->esp_tls_last_esp_err,
               (unsigned int)err->esp_tls_cert_verify_flags,
               err->connect_return_code);
    } else {
      ESP_LOGE(TAG, "MQTT error with no detail payload");
    }
    break;
  }
  default:
    break;
  }
}
#endif

static void cancel_mqtt_retry(void) {
  if (!s_retry_timer)
    return;
  esp_err_t err = esp_timer_stop(s_retry_timer);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(TAG, "Failed to stop MQTT retry timer (%d)", (int)err);
  }
  s_retry_pending = false;
}

static void schedule_mqtt_retry(void);

void ul_mqtt_start(void);

static void mqtt_retry_timer_cb(void *arg) {
  (void)arg;
  s_retry_pending = false;
  ESP_LOGI(TAG, "Retrying MQTT client start");
  ul_mqtt_start();
}

static void schedule_mqtt_retry(void) {
  const uint64_t delay_us = UL_MQTT_RETRY_DELAY_US;
  if (!s_retry_timer) {
    const esp_timer_create_args_t args = {
        .callback = &mqtt_retry_timer_cb,
        .name = "ul_mqtt_retry",
    };
    esp_err_t err = esp_timer_create(&args, &s_retry_timer);
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "Failed to create MQTT retry timer (%d)", (int)err);
      return;
    }
  }

  esp_err_t err = esp_timer_stop(s_retry_timer);
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(TAG, "Failed to stop MQTT retry timer before scheduling (%d)",
             (int)err);
  }

  err = esp_timer_start_once(s_retry_timer, delay_us);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to start MQTT retry timer (%d)", (int)err);
    return;
  }
  s_retry_pending = true;
}

void ul_mqtt_start(void) {
  if (s_client) {
    ESP_LOGW(TAG, "MQTT start requested but client already running");
    return;
  }
  if (!ul_core_is_connected()) {
    ESP_LOGW(TAG, "Network not connected; MQTT not started");
    ul_health_notify_mqtt(false);
    return;
  }

  cancel_mqtt_retry();

#ifndef UL_MQTT_TESTING
  EventGroupHandle_t group = mqtt_state_event_group();
  if (group)
    xEventGroupClearBits(group, UL_MQTT_READY_BIT);
  if (s_publish_ack_queue) {
    xQueueReset(s_publish_ack_queue);
  } else {
    s_publish_ack_queue =
        xQueueCreate(UL_MQTT_PUBLISH_ACK_QUEUE_LENGTH, sizeof(int));
    if (!s_publish_ack_queue) {
      ESP_LOGW(TAG, "Failed to allocate MQTT publish acknowledgment queue");
    }
  }
#endif

  // MQTT runs at modest priority. On the ESP32-C3 all tasks share the
  // single core, so no explicit core assignment is needed.
  esp_mqtt_client_config_t cfg = {
      .broker.address.uri = CONFIG_UL_MQTT_URI,
      .credentials.username = CONFIG_UL_MQTT_USER,
      .credentials.authentication.password = CONFIG_UL_MQTT_PASS,
      .task.priority = 5,
      .task.stack_size = 6144,
  };

  bool dial_override = CONFIG_UL_MQTT_DIAL_HOST[0] != '\0';
  if (dial_override) {
    bool tls = CONFIG_UL_MQTT_USE_TLS;
    esp_mqtt_transport_t transport = transport_from_uri(CONFIG_UL_MQTT_URI, tls);
    bool transport_tls =
        (transport == MQTT_TRANSPORT_OVER_SSL ||
         transport == MQTT_TRANSPORT_OVER_WSS);
    int fallback_port = 1883;
    switch (transport) {
    case MQTT_TRANSPORT_OVER_SSL:
      fallback_port = 8883;
      break;
    case MQTT_TRANSPORT_OVER_TCP:
      fallback_port = 1883;
      break;
    case MQTT_TRANSPORT_OVER_WSS:
      fallback_port = 443;
      break;
    case MQTT_TRANSPORT_OVER_WS:
      fallback_port = 80;
      break;
    default:
      fallback_port = transport_tls ? 8883 : 1883;
      break;
    }
    int default_port = parse_port_from_uri(CONFIG_UL_MQTT_URI, fallback_port);
    int port = CONFIG_UL_MQTT_DIAL_PORT;
    if (port <= 0 || port > 65535)
      port = parse_port_from_uri(CONFIG_UL_MQTT_DIAL_HOST, default_port);
    if (port <= 0 || port > 65535)
      port = default_port;
    cfg.broker.address.uri = NULL;
    static char s_dial_host[128];
    const char *dial_host = CONFIG_UL_MQTT_DIAL_HOST;
    if (parse_host_from_uri(CONFIG_UL_MQTT_DIAL_HOST, s_dial_host,
                            sizeof(s_dial_host))) {
      dial_host = s_dial_host;
    }
    cfg.broker.address.hostname = dial_host;
    cfg.broker.address.port = port;
    cfg.broker.address.transport = transport;
    ESP_LOGI(TAG, "MQTT dialing override host %s:%d (transport %s)",
             dial_host, port, transport_name(transport));
  }

#if CONFIG_UL_MQTT_USE_TLS
#ifndef UL_MQTT_TESTING
  cfg.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
#endif
#if CONFIG_UL_MQTT_TLS_SKIP_COMMON_NAME_CHECK
  cfg.broker.verification.skip_cert_common_name_check = true;
#else
  if (CONFIG_UL_MQTT_TLS_COMMON_NAME[0] != '\0') {
    cfg.broker.verification.common_name = CONFIG_UL_MQTT_TLS_COMMON_NAME;
  } else {
    static char s_tls_host[128];
    if (parse_host_from_uri(CONFIG_UL_MQTT_URI, s_tls_host, sizeof(s_tls_host)))
      cfg.broker.verification.common_name = s_tls_host;
  }
#endif
#endif

  esp_mqtt_client_handle_t client = esp_mqtt_client_init(&cfg);
  if (!client) {
    ESP_LOGE(TAG, "Failed to initialize MQTT client");
    ul_health_notify_mqtt(false);
    schedule_mqtt_retry();
    return;
  }

  esp_err_t register_err =
      esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID,
                                     mqtt_event_handler, NULL);
  if (register_err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to register MQTT event handler (%d)",
             (int)register_err);
    esp_mqtt_client_destroy(client);
    s_client = NULL;
    ul_health_notify_mqtt(false);
    schedule_mqtt_retry();
    return;
  }

  s_client = client;
  esp_err_t start_err = esp_mqtt_client_start(s_client);
  if (start_err != ESP_OK) {
    ESP_LOGE(TAG, "Failed to start MQTT client (%d)", (int)start_err);
    esp_mqtt_client_destroy(s_client);
    s_client = NULL;
    ul_health_notify_mqtt(false);
    schedule_mqtt_retry();
    return;
  }

  ul_health_notify_mqtt(false);
}

void ul_mqtt_stop(void) {
  cancel_mqtt_retry();
  motion_fade_cancel();
#ifndef UL_MQTT_TESTING
  EventGroupHandle_t group = mqtt_state_event_group();
  if (group)
    xEventGroupClearBits(group, UL_MQTT_READY_BIT);
#endif
  if (s_client) {
    esp_mqtt_client_stop(s_client);
    esp_mqtt_client_destroy(s_client);
    s_client = NULL;
  }
  s_ready = false;
  ul_health_notify_mqtt(false);
}

void ul_mqtt_restart(void) {
  ESP_LOGW(TAG, "Restarting MQTT client");
  bool had_client = s_client != NULL;
  ul_mqtt_stop();
  if (!ul_core_is_connected()) {
    ESP_LOGW(TAG, "Skip MQTT restart (network offline)");
    return;
  }
  if (had_client) {
    vTaskDelay(pdMS_TO_TICKS(200));
  }
  ul_mqtt_start();
}

bool ul_mqtt_is_connected(void) { return s_ready; }

bool ul_mqtt_is_ready(void) { return s_ready; }

bool ul_mqtt_wait_for_ready(TickType_t timeout_ticks) {
#ifdef UL_MQTT_TESTING
  (void)timeout_ticks;
  return s_ready;
#else
  if (s_ready)
    return true;
  EventGroupHandle_t group = mqtt_state_event_group();
  if (!group)
    return false;
  EventBits_t bits =
      xEventGroupWaitBits(group, UL_MQTT_READY_BIT, pdFALSE, pdFALSE, timeout_ticks);
  return (bits & UL_MQTT_READY_BIT) != 0;
#endif
}

#ifndef UL_MQTT_TESTING
void ul_mqtt_publish_status_now(void) { publish_status_snapshot(); }

void ul_mqtt_run_local(const char *path, const char *json) {
  if (!path || !json)
    return;
  cJSON *root = cJSON_Parse(json);
  if (!root)
    return;
  size_t payload_len = strlen(json);
  if (starts_with(path, "ws/set")) {
    override_index_from_path(root, path, "ws/set", "strip");
    int strip = 0;
    bool applied = handle_cmd_ws_set(root, &strip);
    if (applied) {
      if (payload_len > 0) {
        ul_state_record_ws(strip, json, payload_len);
      }
    }
  } else if (starts_with(path, "rgb/set")) {
    override_index_from_path(root, path, "rgb/set", "strip");
    int strip = 0;
    bool applied = handle_cmd_rgb_set(root, &strip);
    if (applied) {
      if (payload_len > 0) {
        ul_state_record_rgb(strip, json, payload_len);
      }
    }
  } else if (starts_with(path, "white/set")) {
    override_index_from_path(root, path, "white/set", "channel");
    int channel = 0;
    bool applied = handle_cmd_white_set(root, &channel);
    if (applied) {
      if (payload_len > 0) {
        ul_state_record_white(channel, json, payload_len);
      }
    }
  }
  cJSON_Delete(root);
}
#endif

#ifdef UL_MQTT_TESTING
esp_mqtt_client_handle_t ul_mqtt_test_get_client_handle(void) { return s_client; }
bool ul_mqtt_test_retry_pending(void) { return s_retry_pending; }
#endif

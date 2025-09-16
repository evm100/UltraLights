#include "ul_mqtt.h"
#include "cJSON.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "sdkconfig.h"
#include "ul_core.h"
#include "ul_ota.h"
#include "ul_white_engine.h"
#include "ul_ws_engine.h"
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static const char *TAG = "ul_mqtt";
static esp_mqtt_client_handle_t s_client = NULL;
static bool s_ready = false;

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
static void publish_json(const char *topic, const char *json) {

  if (!s_client || !ul_core_is_connected())
    return;
  esp_mqtt_client_publish(s_client, topic, json, 0, 1, 0);
}

// Build and publish status JSON snapshot
static void publish_status_snapshot(void) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
  cJSON_AddStringToObject(root, "node", ul_core_get_node_id());

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
      cJSON_AddNumberToObject(o, "gpio", st.gpio);
      cJSON_AddNumberToObject(o, "pwm_hz", st.pwm_hz);
      cJSON_AddItemToArray(jw, o);
    }
  }
  cJSON_AddItemToObject(root, "white", jw);

  // OTA (static fields from Kconfig)
  cJSON *jota = cJSON_CreateObject();
  cJSON_AddStringToObject(jota, "manifest_url", CONFIG_UL_OTA_MANIFEST_URL);
  cJSON_AddItemToObject(root, "ota", jota);

  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

void ul_mqtt_publish_status(void) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  publish_json(topic, "{\"status\":\"ok\"}");
}

// Publish confirmation for ws/set including echo of effect parameters
static void publish_ws_ack(int strip, const char *effect, cJSON *params,
                           bool ok) {
  char topic[128];
  snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
  cJSON *root = cJSON_CreateObject();
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
  char *json = cJSON_PrintUnformatted(root);
  publish_json(topic, json);
  cJSON_free(json);
  cJSON_Delete(root);
}

static void handle_cmd_ws_set(cJSON *root) {
  int strip = 0;
  cJSON *jstrip = cJSON_GetObjectItem(root, "strip");
  if (jstrip && cJSON_IsNumber(jstrip))
    strip = jstrip->valueint;

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
}

static void handle_cmd_white_set(cJSON *root) { ul_white_apply_json(root); }
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
      override_index_from_path(root, sub, "ws/set", "strip");
      handle_cmd_ws_set(root);
    } else if (starts_with(sub, "ota/check")) {
      ul_mqtt_publish_status();
      ul_ota_check_now(true);
      publish_status_snapshot();
    }
    else if (starts_with(sub, "white/set")) {
      override_index_from_path(root, sub, "white/set", "channel");
      handle_cmd_white_set(root);
      ul_mqtt_publish_status();
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
    if (ul_core_is_connected()) {
      char topic[128];
      snprintf(topic, sizeof(topic), "ul/%s/cmd/#", ul_core_get_node_id());
      esp_mqtt_client_subscribe(s_client, topic, 1);
      // Also allow broadcast to any node if you publish to ul/+/cmd/#
      esp_mqtt_client_subscribe(s_client, "ul/+/cmd/#", 0);
    }
    break;
  }
  case MQTT_EVENT_DISCONNECTED:
    ESP_LOGW(TAG, "MQTT disconnected");
    s_ready = false;
    break;
  case MQTT_EVENT_DATA:
    on_message(event);
    break;
  default:
    break;
  }
}

void ul_mqtt_start(void) {
  if (!ul_core_is_connected()) {
    ESP_LOGW(TAG, "Network not connected; MQTT not started");
    return;
  }
  // MQTT runs at modest priority. On the ESP32-C3 all tasks share the
  // single core, so no explicit core assignment is needed.
  esp_mqtt_client_config_t cfg = {
      .broker.address.uri = CONFIG_UL_MQTT_URI,
      .credentials.username = CONFIG_UL_MQTT_USER,
      .credentials.authentication.password = CONFIG_UL_MQTT_PASS,
      .task.priority = 5,
      .task.stack_size = 6144,
  };
  s_client = esp_mqtt_client_init(&cfg);
  esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler,
                                 NULL);
  esp_mqtt_client_start(s_client);
}

void ul_mqtt_stop(void) {
  if (!s_client)
    return;
  esp_mqtt_client_stop(s_client);
  esp_mqtt_client_destroy(s_client);
  s_client = NULL;
  s_ready = false;
}

bool ul_mqtt_is_connected(void) { return s_ready; }

bool ul_mqtt_is_ready(void) { return s_ready; }

void ul_mqtt_publish_status_now(void) { publish_status_snapshot(); }

void ul_mqtt_run_local(const char *path, const char *json) {
  if (!path || !json)
    return;
  cJSON *root = cJSON_Parse(json);
  if (!root)
    return;
  if (starts_with(path, "ws/set")) {
    override_index_from_path(root, path, "ws/set", "strip");
    handle_cmd_ws_set(root);
  } else if (starts_with(path, "white/set")) {
    override_index_from_path(root, path, "white/set", "channel");
    handle_cmd_white_set(root);
  }
  cJSON_Delete(root);
}

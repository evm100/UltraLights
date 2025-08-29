#include "ul_mqtt.h"
#include "sdkconfig.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "ul_core.h"
#include "ul_ws_engine.h"
#include "ul_white_engine.h"
#include "ul_sensors.h"
#include "ul_ota.h"
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include "cJSON.h"
#include "esp_timer.h"

static const char* TAG = "ul_mqtt";
static esp_mqtt_client_handle_t s_client = NULL;
static bool s_ready = false;

// JSON helpers (defined later)
static bool j_is_int_in(cJSON* obj, const char* key, int minv, int maxv, int* out);
static bool j_is_bool(cJSON* obj, const char* key, bool* out);

static int starts_with(const char* s, const char* pfx) {
    return strncmp(s, pfx, strlen(pfx)) == 0;
}

// Helper to publish JSON
static void publish_json(const char* topic, const char* json) {

    if (!s_client) return;
    esp_mqtt_client_publish(s_client, topic, json, 0, 1, 0);
}


// Build and publish status JSON snapshot
static void publish_status_snapshot(void) {
    char topic[128];
    snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
    cJSON* root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "node", ul_core_get_node_id());

    // uptime
    cJSON_AddNumberToObject(root, "uptime_s", esp_timer_get_time()/1000000);

    // WS strips
    cJSON* jws = cJSON_CreateArray();
    for (int i=0;i<4;i++) {
        ul_ws_strip_status_t st;
        if (ul_ws_get_status(i, &st)) {
            cJSON* o = cJSON_CreateObject();
            cJSON_AddNumberToObject(o, "strip", i);
            cJSON_AddBoolToObject(o, "enabled", st.enabled);
            cJSON_AddBoolToObject(o, "power", st.power);
            cJSON_AddStringToObject(o, "effect", st.effect);
            cJSON_AddNumberToObject(o, "brightness", st.brightness);
            cJSON_AddNumberToObject(o, "pixels", st.pixels);
            cJSON_AddNumberToObject(o, "gpio", st.gpio);
            cJSON_AddNumberToObject(o, "fps", st.fps);
            cJSON* col = cJSON_CreateIntArray((int[]){st.color[0],st.color[1],st.color[2]},3);
            cJSON_AddItemToObject(o, "color", col);
            cJSON_AddItemToArray(jws, o);
        }
    }
    cJSON_AddItemToObject(root, "ws", jws);

    // White channels
    cJSON* jw = cJSON_CreateArray();
    for (int i=0;i<4;i++) {
        ul_white_ch_status_t st;
        if (ul_white_get_status(i, &st)) {
            cJSON* o = cJSON_CreateObject();
            cJSON_AddNumberToObject(o, "channel", i);
            cJSON_AddBoolToObject(o, "enabled", st.enabled);
            cJSON_AddBoolToObject(o, "power", st.power);
            cJSON_AddStringToObject(o, "effect", st.effect);
            cJSON_AddNumberToObject(o, "brightness", st.brightness);
            cJSON_AddNumberToObject(o, "gpio", st.gpio);
            cJSON_AddNumberToObject(o, "pwm_hz", st.pwm_hz);
            cJSON_AddItemToArray(jw, o);
        }
    }
    cJSON_AddItemToObject(root, "white", jw);

    // Sensors
    ul_sensor_status_t ss; ul_sensors_get_status(&ss);
    cJSON* jsens = cJSON_CreateObject();
    cJSON_AddNumberToObject(jsens, "cooldown_s", ss.cooldown_s);
    cJSON_AddBoolToObject(jsens, "pir_enabled", ss.pir_enabled);
    cJSON_AddBoolToObject(jsens, "ultra_enabled", ss.ultra_enabled);
    cJSON_AddBoolToObject(jsens, "pir_active", ss.pir_active);
    cJSON_AddBoolToObject(jsens, "ultra_active", ss.ultra_active);
    cJSON_AddNumberToObject(jsens, "ultra_near_mm", ss.near_threshold_mm);
    cJSON_AddItemToObject(root, "sensors", jsens);

    // OTA (static fields from Kconfig)
    cJSON* jota = cJSON_CreateObject();
    cJSON_AddStringToObject(jota, "manifest_url", CONFIG_UL_OTA_MANIFEST_URL);
    cJSON_AddItemToObject(root, "ota", jota);

    char* json = cJSON_PrintUnformatted(root);
    publish_json(topic, json);
    cJSON_free(json);
    cJSON_Delete(root);
}

void ul_mqtt_publish_status(void)
{
    char topic[128];
    snprintf(topic, sizeof(topic), "ul/%s/evt/status", ul_core_get_node_id());
    publish_json(topic, "{\"status\":\"ok\"}");
}

void ul_mqtt_publish_motion(const char* sid, const char* state)
{
    char topic[128];
    char payload[160];
    snprintf(topic, sizeof(topic), "ul/%s/evt/sensor/motion", ul_core_get_node_id());
    snprintf(payload, sizeof(payload), "{\"sid\":\"%s\",\"state\":\"%s\"}", sid, state);
    publish_json(topic, payload);
}

static void handle_cmd_ws_set(cJSON* root) {
    ul_ws_apply_json(root);
}

static void handle_cmd_ws_power(cJSON* root) {
    int strip = cJSON_GetObjectItem(root, "strip") ? cJSON_GetObjectItem(root, "strip")->valueint : 0;
    cJSON* jon = cJSON_GetObjectItem(root, "on");
    bool on = (jon && cJSON_IsBool(jon)) ? cJSON_IsTrue(jon) : true;
    ul_ws_power(strip, on);

    ul_mqtt_publish_status_now();
}


// ---- Minimal JSON schema helpers ----
static bool j_is_int_in(cJSON* obj, const char* key, int minv, int maxv, int* out) {
    cJSON* j = cJSON_GetObjectItem(obj, key);
    if (!j || !cJSON_IsNumber(j)) return false;
    int v = j->valueint;
    if (v < minv || v > maxv) return false;
    if (out) *out = v;
    return true;
}
static bool j_is_bool(cJSON* obj, const char* key, bool* out) {
    cJSON* j = cJSON_GetObjectItem(obj, key);
    if (!j || !cJSON_IsBool(j)) return false;
    if (out) *out = cJSON_IsTrue(j);
    return true;
}

static void handle_cmd_sensor_cooldown(cJSON* root) {

    cJSON* js = cJSON_GetObjectItem(root, "seconds");
    int s; if (j_is_int_in(root, "seconds", 10, 3600, &s)) { ul_sensors_set_cooldown(s);
    ul_mqtt_publish_status_now();
} else { ESP_LOGW(TAG,"invalid seconds"); }
}


static void handle_cmd_white_set(cJSON* root) {
    ul_white_apply_json(root);
}
static void handle_cmd_white_power(cJSON* root) {
    int ch=0; j_is_int_in(root, "channel", 0, 3, &ch);
    bool on=false; if (j_is_bool(root, "on", &on)) ul_white_power(ch, on);

    ul_mqtt_publish_status_now();
}

static void on_message(esp_mqtt_event_handle_t event)
{
    // topic expected: ul/<node>/cmd/...
    char node[64] = {0};
    const char* topic = event->topic;
    int tlen = event->topic_len;
    if (!topic || tlen <= 0) return;

    // Extract node id segment
    // pattern: "ul/xxxx/cmd/..."
    const char* p = memchr(topic, '/', tlen);
    if (!p) return;
    p++; // after "ul/"
    const char* slash2 = memchr(p, '/', (topic+tlen) - p);
    if (!slash2) return;
    int node_len = (int)(slash2 - p);
    if (node_len <= 0 || node_len >= (int)sizeof(node)) return;
    memcpy(node, p, node_len); node[node_len] = 0;

    if (strcmp(node, ul_core_get_node_id()) != 0 && strcmp(node, "+") != 0) {
        // not for us
        return;
    }

    // Grab command path after "ul/<node>/"
    const char* cmdroot = slash2 + 1;
    int cmdlen = (topic + tlen) - cmdroot;

    // Parse JSON
    cJSON* root = cJSON_ParseWithLength(event->data, event->data_len);
    if (!root) {
        ESP_LOGW(TAG, "Invalid JSON payload");
        return;
    }

    if (cmdlen >= 3 && strncmp(cmdroot, "cmd", 3)==0) {
        const char* sub = cmdroot + 4; // skip "cmd/"
        if (starts_with(sub, "ws/set")) {
            handle_cmd_ws_set(root); publish_status_snapshot();
        } else if (starts_with(sub, "ws/power")) {
            handle_cmd_ws_power(root); publish_status_snapshot();
        } else if (starts_with(sub, "sensor/cooldown")) {
            handle_cmd_sensor_cooldown(root); publish_status_snapshot();
        } else if (starts_with(sub, "ota/check")) { ul_mqtt_publish_status_now(); 
            ul_ota_check_now(true); publish_status_snapshot();
        } else if (starts_with(sub, "white/set")) { handle_cmd_white_set(root); publish_status_snapshot(); } else if (starts_with(sub, "white/power")) { handle_cmd_white_power(root); publish_status_snapshot(); } else {
            ESP_LOGW(TAG, "Unknown cmd path: %.*s", cmdlen, cmdroot);
        }
    }
    cJSON_Delete(root);
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED: {
            ESP_LOGI(TAG, "MQTT connected");
            s_ready = true;
            char topic[128];
            snprintf(topic, sizeof(topic), "ul/%s/cmd/#", ul_core_get_node_id());
            esp_mqtt_client_subscribe(s_client, topic, 1);
            // Also allow broadcast to any node if you publish to ul/+/cmd/#
            esp_mqtt_client_subscribe(s_client, "ul/+/cmd/#", 0);
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

void ul_mqtt_start(void)
{
    // Pin MQTT networking to core 0 with modest priority so core 1 can
    // focus on time-critical LED driving.
    esp_mqtt_client_config_t cfg = {
        .broker.address.uri = CONFIG_UL_MQTT_URI,
        .credentials.username = CONFIG_UL_MQTT_USER,
        .credentials.authentication.password = CONFIG_UL_MQTT_PASS,
        .task.priority = 5,
        .task.stack_size = 6144,
        .task.core_id = 0,
    };
    s_client = esp_mqtt_client_init(&cfg);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);
}

bool ul_mqtt_is_ready(void) { return s_ready; }

void ul_mqtt_publish_status_now(void){ publish_status_snapshot(); }

#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "sdkconfig.h"
#include "ul_board.h"
#include "ul_contract.h"
#include "ul_core.h"
#include "ul_health.h"
#include "ul_identity.h"
#include "ul_mqtt.h"
#include "ul_wifi.h"

static const char *TAG = "ul_mqtt";

#define MAX_PAYLOAD       8192
#define STATE_MIN_US      (200 * 1000)        /* at most 5 state messages per second per output */
#define FLUSH_US          (50 * 1000)
#define HEALTH_US         (60LL * 1000 * 1000)
#define CMD_MAX_AGE_S     30

static esp_mqtt_client_handle_t s_client;
static bool s_connected;
static char s_prefix[24];      /* ul/v1/d/<id>/ */
static char s_will_topic[40];
static uint32_t s_reconnects;
static int64_t s_last_pub[UL_MAX_OUTPUTS];
static bool s_pending[UL_MAX_OUTPUTS];
static int s_seen_rev = -1;
static esp_timer_handle_t s_flush_timer, s_health_timer, s_restart_timer;
static ul_mqtt_cmd_handler_t s_handlers[UL_OP_SELFTEST + 1];

/* incoming message being reassembled */
static char *s_rx;
static char s_rx_topic[64];
static int s_rx_len;

static const char WILL[] = "{\"online\":false}";

/* ---------- publishing ---------- */

static void publish(const char *suffix, const char *payload, int qos, bool retain)
{
    if (!s_client || !payload) {
        return;
    }
    char topic[64];
    snprintf(topic, sizeof(topic), "%s%s", s_prefix, suffix);
    esp_mqtt_client_enqueue(s_client, topic, payload, (int)strlen(payload), qos, retain, true);
}

static void publish_state(int idx)
{
    ul_output_def_t d;
    ul_state_msg_t st;
    if (!ul_core_output_def(idx, &d) || !ul_core_output_state(idx, &st)) {
        return;
    }
    char *json = ul_state_encode(&st);
    char suffix[32];
    snprintf(suffix, sizeof(suffix), "state/%s", d.key);
    publish(suffix, json, 1, true);
    free(json);
    s_last_pub[idx] = esp_timer_get_time();
    s_pending[idx] = false;
}

static void publish_info(void)
{
    const ul_board_desc_t *b = ul_board_get();
    const ul_board_desc_t *raw = ul_board_raw();
    ul_config_t *cfg = calloc(1, sizeof(*cfg));
    ul_board_desc_t *empty = NULL;
    if (!cfg) {
        return;
    }
    if (!b && raw) {
        /* Rejected board: report its name and channels, but no outputs are driven. */
        b = raw;
    } else if (!b) {
        empty = calloc(1, sizeof(*empty));
        if (!empty) {
            free(cfg);
            return;
        }
        empty->chip = ul_board_running_chip();
        snprintf(empty->board, sizeof(empty->board), "none");
        b = empty;
    }
    ul_core_config(cfg);
    ul_info_t info = {
        .id = ul_identity_device_id(), .chip = ul_board_running_chip(),
        .fw = esp_app_get_description()->version, .profile = CONFIG_UL_PROFILE,
        .idf = esp_get_idf_version(), .board = b, .board_crc = ul_board_crc(),
        .outputs = cfg, .config_rev = cfg->rev, .config_error = ul_core_config_error(),
    };
    esp_read_mac(info.mac, ESP_MAC_WIFI_STA);
    char *json = ul_info_encode(&info);
    publish("info", json, 1, true);
    free(json);
    free(cfg);
    free(empty);
    s_seen_rev = info.config_rev;
}

static void publish_health(void)
{
    ul_health_t h;
    ul_health_collect(&h);
    h.mqtt_reconnects = s_reconnects;
    char *json = ul_health_encode(&h);
    publish("health", json, 0, false);
    free(json);
}

esp_err_t ul_mqtt_publish_evt(const ul_evt_t *e)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }
    char *json = ul_evt_encode(e);
    publish("evt", json, 1, false);
    free(json);
    return json ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t ul_mqtt_cmd_result(const char *cmd_id, bool ok, const char *error, const char *detail, int progress)
{
    ul_evt_t e = {.type = UL_EVT_CMD_RESULT, .ok = ok, .error = error, .detail = detail, .progress = progress};
    snprintf(e.cmd_id, sizeof(e.cmd_id), "%s", cmd_id);
    return ul_mqtt_publish_evt(&e);
}

bool ul_mqtt_connected(void)
{
    return s_connected;
}

void ul_mqtt_register_cmd(ul_op_t op, ul_mqtt_cmd_handler_t handler)
{
    if ((unsigned)op <= UL_OP_SELFTEST) {
        s_handlers[op] = handler;
    }
}

/* ---------- timers ---------- */

static void flush(void *arg)
{
    if (!s_connected) {
        return;
    }
    int64_t now = esp_timer_get_time();
    int n = ul_core_output_count();
    for (int i = 0; i < n && i < UL_MAX_OUTPUTS; i++) {
        if (s_pending[i] && now - s_last_pub[i] >= STATE_MIN_US) {
            publish_state(i);
        }
    }
}

static void health_tick(void *arg)
{
    if (s_connected) {
        publish_health();
    }
}

static void restart(void *arg)
{
    esp_restart();
}

static void restart_soon(void)
{
    esp_timer_start_once(s_restart_timer, 1000 * 1000);
}

/* ---------- incoming ---------- */

static void handle_cmd(const char *payload, int len)
{
    ul_cmd_t *c = calloc(1, sizeof(*c));
    char err[96];
    if (!c) {
        return;
    }
    if (ul_cmd_decode(payload, (size_t)len, c, err, sizeof(err)) != 0) {
        ESP_LOGW(TAG, "bad cmd: %s", err);
        ul_evt_t e = {.type = UL_EVT_ERROR, .code = "bad_cmd", .detail = err};
        ul_mqtt_publish_evt(&e);
        free(c);
        return;
    }
    ESP_LOGI(TAG, "cmd %s (%s)", ul_op_name(c->op), c->id);
    if (c->ts && ul_wifi_time_synced() && (int64_t)time(NULL) - c->ts > CMD_MAX_AGE_S) {
        ul_mqtt_cmd_result(c->id, false, "stale", NULL, -1);
        free(c);
        return;
    }
    esp_err_t r;
    switch (c->op) {
    case UL_OP_IDENTIFY:
        r = ul_core_identify(c->out[0] ? c->out : NULL, c->ms);
        ul_mqtt_cmd_result(c->id, r == ESP_OK, r == ESP_ERR_NOT_FOUND ? "no_such_output" : r ? "failed" : NULL, NULL, -1);
        break;
    case UL_OP_REBOOT:
        ul_mqtt_cmd_result(c->id, true, NULL, NULL, -1);
        restart_soon();
        break;
    case UL_OP_WIFI_RESET:
        ul_mqtt_cmd_result(c->id, true, NULL, NULL, -1);
        ul_wifi_forget();
        restart_soon();
        break;
    case UL_OP_FACTORY_RESET:
        ul_mqtt_cmd_result(c->id, true, NULL, NULL, -1);
        ul_wifi_forget();
        ul_core_forget();
        ul_identity_forget();
        restart_soon();
        break;
    case UL_OP_PULSE:
        r = ul_core_pulse(c->out, c->ms);
        ul_mqtt_cmd_result(c->id, r == ESP_OK,
                           r == ESP_ERR_NOT_FOUND ? "no_such_output" : r == ESP_ERR_NOT_SUPPORTED ? "not_a_solenoid" : NULL,
                           NULL, -1);
        break;
    default:
        if (s_handlers[c->op]) {
            r = s_handlers[c->op](c);
            if (r != ESP_OK) {
                ul_mqtt_cmd_result(c->id, false, esp_err_to_name(r), NULL, -1);
            }
        } else {
            ul_mqtt_cmd_result(c->id, false, "unsupported", NULL, -1);
        }
        break;
    }
    free(c);
}

static void dispatch(const char *topic, const char *payload, int len)
{
    size_t pl = strlen(s_prefix);
    if (strncmp(topic, s_prefix, pl) != 0) {
        return;
    }
    const char *t = topic + pl;
    char err[96];
    if (strcmp(t, "config") == 0) {
        ul_core_apply_config(payload, (size_t)len);
    } else if (strncmp(t, "set/", 4) == 0 || strncmp(t, "live/", 5) == 0) {
        bool live = t[0] == 'l';
        const char *key = t + (live ? 5 : 4);
        if (len == 0) {
            return; /* retained message cleared */
        }
        ul_state_msg_t m;
        int rc = live ? ul_live_decode(payload, (size_t)len, &m, err, sizeof(err))
                      : ul_set_decode(payload, (size_t)len, &m, err, sizeof(err));
        if (rc != 0) {
            ESP_LOGW(TAG, "bad %s for %s: %s", live ? "live" : "set", key, err);
            ul_evt_t e = {.type = UL_EVT_ERROR, .code = "bad_message", .detail = err, .out = key};
            ul_mqtt_publish_evt(&e);
            return;
        }
        esp_err_t r = ul_core_apply(key, &m, live);
        if (r == ESP_ERR_NOT_FOUND) {
            ESP_LOGD(TAG, "no output %s", key); /* normal for retained sets of an older mapping */
        }
    } else if (strcmp(t, "cmd") == 0) {
        if (len) {
            handle_cmd(payload, len);
        }
    }
}

static void on_data(esp_mqtt_event_handle_t ev)
{
    if (ev->current_data_offset == 0) {
        free(s_rx);
        s_rx = NULL;
        if (ev->total_data_len > MAX_PAYLOAD || ev->topic_len >= (int)sizeof(s_rx_topic)) {
            ESP_LOGW(TAG, "dropping oversized message (%d bytes)", ev->total_data_len);
            return;
        }
        memcpy(s_rx_topic, ev->topic, (size_t)ev->topic_len);
        s_rx_topic[ev->topic_len] = '\0';
        s_rx = malloc((size_t)ev->total_data_len + 1);
        s_rx_len = ev->total_data_len;
        if (!s_rx) {
            return;
        }
    }
    if (!s_rx) {
        return;
    }
    memcpy(s_rx + ev->current_data_offset, ev->data, (size_t)ev->data_len);
    if (ev->current_data_offset + ev->data_len < s_rx_len) {
        return; /* more fragments coming */
    }
    s_rx[s_rx_len] = '\0';
    dispatch(s_rx_topic, s_rx, s_rx_len);
    free(s_rx);
    s_rx = NULL;
}

static void subscribe(const char *suffix, int qos)
{
    char topic[64];
    snprintf(topic, sizeof(topic), "%s%s", s_prefix, suffix);
    esp_mqtt_client_subscribe(s_client, topic, qos);
}

static void on_mqtt(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_mqtt_event_handle_t ev = data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "connected");
        s_connected = true;
        /* config first, so retained sets arrive for the right mapping */
        subscribe("config", 1);
        subscribe("set/+", 1);
        subscribe("live/+", 0);
        subscribe("cmd", 1);
        publish("status", "{\"online\":true}", 1, true);
        publish_info();
        publish_health();
        for (int i = 0; i < ul_core_output_count(); i++) {
            publish_state(i);
        }
        esp_timer_start_periodic(s_flush_timer, FLUSH_US);
        esp_event_post(UL_EVENT, UL_EV_MQTT_UP, NULL, 0, 0);
        break;
    case MQTT_EVENT_DISCONNECTED:
        if (s_connected) {
            s_reconnects++;
            esp_event_post(UL_EVENT, UL_EV_MQTT_DOWN, NULL, 0, 0);
        }
        s_connected = false;
        esp_timer_stop(s_flush_timer);
        ESP_LOGW(TAG, "disconnected");
        break;
    case MQTT_EVENT_DATA:
        on_data(ev);
        break;
    case MQTT_EVENT_ERROR:
        if (ev->error_handle && ev->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
            ESP_LOGW(TAG, "transport error: tls 0x%x, sock errno %d", (unsigned)ev->error_handle->esp_tls_last_esp_err,
                     ev->error_handle->esp_transport_sock_errno);
        } else if (ev->error_handle && ev->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
            ESP_LOGW(TAG, "broker refused the connection (%d)", ev->error_handle->connect_return_code);
        }
        break;
    default:
        break;
    }
}

/* ---------- ul_core events ---------- */

static void on_core(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    switch (id) {
    case UL_EV_STATE: {
        int idx = *(const int *)data;
        if (idx < 0 || idx >= UL_MAX_OUTPUTS || !s_connected) {
            break;
        }
        if (esp_timer_get_time() - s_last_pub[idx] >= STATE_MIN_US) {
            publish_state(idx);
        } else {
            s_pending[idx] = true;
        }
        break;
    }
    case UL_EV_INFO:
        if (s_connected) {
            int before = s_seen_rev;
            publish_info();
            if (s_seen_rev != before) {
                /* New mapping: ask for the retained sets again, so new output keys get theirs. */
                char topic[64];
                snprintf(topic, sizeof(topic), "%sset/+", s_prefix);
                esp_mqtt_client_unsubscribe(s_client, topic);
                esp_mqtt_client_subscribe(s_client, topic, 1);
            }
        }
        break;
    case UL_EV_ERROR: {
        const ul_error_t *e = data;
        ul_evt_t ev = {.type = UL_EVT_ERROR, .code = e->code, .detail = e->detail[0] ? e->detail : NULL,
                       .out = e->out[0] ? e->out : NULL};
        ul_mqtt_publish_evt(&ev);
        break;
    }
    case UL_EV_BUTTON: {
        ul_evt_t ev = {.type = UL_EVT_BUTTON, .action = *(const ul_btn_action_t *)data};
        ul_mqtt_publish_evt(&ev);
        break;
    }
    case UL_EV_IDENTITY_READY:
        if (!s_client) {
            const char *host = ul_identity_mqtt_host();
            const char *ca = ul_identity_server_ca();
            esp_mqtt_client_config_t cfg = {
                .broker.address.hostname = host,
                .broker.address.port = ul_identity_mqtt_port(),
                .broker.address.transport = MQTT_TRANSPORT_OVER_SSL,
                .broker.verification.crt_bundle_attach = ca ? NULL : esp_crt_bundle_attach,
                .broker.verification.certificate = ca,
                .credentials.client_id = ul_identity_device_id(),
                .credentials.authentication.certificate = ul_identity_cert_chain(),
                .credentials.authentication.key = ul_identity_key(),
                .session.keepalive = 30,
                .session.last_will = {.topic = s_will_topic, .msg = WILL, .msg_len = sizeof(WILL) - 1,
                                      .qos = 1, .retain = 1},
                .network.reconnect_timeout_ms = 5000,
                .buffer.size = 2048,
                .buffer.out_size = 2048,
                .task.stack_size = 6144,
            };
            s_client = esp_mqtt_client_init(&cfg);
            if (!s_client) {
                ESP_LOGE(TAG, "client init failed");
                break;
            }
            esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, on_mqtt, NULL);
            esp_mqtt_client_start(s_client);
            ESP_LOGI(TAG, "connecting to %s:%u as %s", host, ul_identity_mqtt_port(), ul_identity_device_id());
        }
        break;
    default:
        break;
    }
}

static esp_err_t ul_mqtt_init(void)
{
    const char *id = ul_identity_device_id();
    if (!id) {
        return ESP_OK; /* no descriptor, nothing to connect as */
    }
    snprintf(s_prefix, sizeof(s_prefix), "ul/v1/d/%s/", id);
    snprintf(s_will_topic, sizeof(s_will_topic), "%sstatus", s_prefix);
    const esp_timer_create_args_t f = {.callback = flush, .name = "ul_mqtt_flush"};
    const esp_timer_create_args_t h = {.callback = health_tick, .name = "ul_mqtt_health"};
    const esp_timer_create_args_t r = {.callback = restart, .name = "ul_mqtt_restart"};
    ESP_ERROR_CHECK(esp_timer_create(&f, &s_flush_timer));
    ESP_ERROR_CHECK(esp_timer_create(&h, &s_health_timer));
    ESP_ERROR_CHECK(esp_timer_create(&r, &s_restart_timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(s_health_timer, HEALTH_US));
    ESP_ERROR_CHECK(esp_event_handler_register(UL_EVENT, ESP_EVENT_ANY_ID, on_core, NULL));
    if (ul_identity_ready()) {
        /* Its event may have gone out before this handler existed. Re-post rather than call
         * directly, so client creation only ever runs on the event task. */
        esp_event_post(UL_EVENT, UL_EV_IDENTITY_READY, NULL, 0, 0);
    }
    return ESP_OK;
}

/* Runs after ul_identity (30), which may already be ready. */
UL_MODULE_REGISTER(mqtt, "ul_mqtt", UL_STAGE_SERVICE, 40, ul_mqtt_init)

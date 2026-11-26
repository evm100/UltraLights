#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "ul_board.h"
#include "ul_contract.h"
#include "ul_core.h"

static const char *TAG = "ul_core";

ESP_EVENT_DEFINE_BASE(UL_EVENT);

#define NVS_NS            "ul_core"
#define SAVE_DELAY_US     (3 * 1000 * 1000)
#define MAX_MODULES       24
#define MAX_FADE_POINTS   8
#define IDENTIFY_TICK_US  (250 * 1000)

typedef struct {
    char id[UL_KEY_SZ];
    const ul_channel_ops_t *ops;
    void *ctx;
    ul_ch_type_t type;
} chan_t;

typedef struct {
    ul_out_state_t st;
    int ch[UL_ROLE_COUNT];        /* index into s_chans, -1 = none */
    ul_state_msg_t shown;         /* what the hardware was last told */
    int64_t pulse_end_us;         /* 0 = no pulse */
} out_rt_t;

static SemaphoreHandle_t s_lock;
static const ul_board_desc_t *s_board;
static chan_t s_chans[UL_MAX_CHANNELS];
static int s_nchan;
static ul_config_t s_cfg;
static out_rt_t s_rt[UL_MAX_OUTPUTS];
static char s_cfg_err[160];
static esp_timer_handle_t s_save_timer, s_pulse_timer, s_identify_timer;
static int64_t s_identify_end_us;
static int s_identify_idx = -2; /* -2 none, -1 all, else index */
static bool s_identify_phase;

static const ul_module_t *s_modules[MAX_MODULES];
static int s_nmodules;

#define LOCK() xSemaphoreTakeRecursive(s_lock, portMAX_DELAY)
#define UNLOCK() xSemaphoreGiveRecursive(s_lock)

/* ---------- modules and events ---------- */

void ul_module_register(const ul_module_t *m)
{
    if (s_nmodules < MAX_MODULES) {
        s_modules[s_nmodules++] = m;
    }
}

static int module_cmp(const void *a, const void *b)
{
    const ul_module_t *x = *(const ul_module_t *const *)a, *y = *(const ul_module_t *const *)b;
    if (x->stage != y->stage) {
        return (int)x->stage - (int)y->stage;
    }
    return x->order - y->order;
}

static void run_stage(ul_stage_t stage)
{
    for (int i = 0; i < s_nmodules; i++) {
        const ul_module_t *m = s_modules[i];
        if (m->stage != stage) {
            continue;
        }
        esp_err_t err = m->init();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "module %s failed: %s", m->name, esp_err_to_name(err));
        } else {
            ESP_LOGI(TAG, "module %s ready", m->name);
        }
    }
}

void ul_core_report_error(const char *code, const char *out, const char *fmt, ...)
{
    ul_error_t e = {0};
    snprintf(e.code, sizeof(e.code), "%s", code);
    if (out) {
        snprintf(e.out, sizeof(e.out), "%s", out);
    }
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(e.detail, sizeof(e.detail), fmt, ap);
    va_end(ap);
    ESP_LOGW(TAG, "error %s: %s", e.code, e.detail);
    esp_event_post(UL_EVENT, UL_EV_ERROR, &e, sizeof(e), 0);
}

static void post_state(int idx)
{
    esp_event_post(UL_EVENT, UL_EV_STATE, &idx, sizeof(idx), 0);
}

/* ---------- channels ---------- */

esp_err_t ul_core_register_channel(const char *id, const ul_channel_ops_t *ops, void *ctx)
{
    if (!s_board || s_nchan >= UL_MAX_CHANNELS) {
        return ESP_ERR_INVALID_STATE;
    }
    int bi = ul_board_find_channel(s_board, id);
    if (bi < 0) {
        return ESP_ERR_NOT_FOUND;
    }
    chan_t *c = &s_chans[s_nchan++];
    snprintf(c->id, sizeof(c->id), "%s", id);
    c->ops = ops;
    c->ctx = ctx;
    c->type = s_board->ch[bi].type;
    return ESP_OK;
}

static int find_chan(const char *id)
{
    for (int i = 0; i < s_nchan; i++) {
        if (strcmp(s_chans[i].id, id) == 0) {
            return i;
        }
    }
    return -1;
}

static void chan_off(int ci)
{
    const chan_t *c = &s_chans[ci];
    static const float zero4[1][4] = {{0}};
    float zero = 0;
    if (c->ops->set_duty) {
        c->ops->set_duty(c->ctx, &zero, 1, 0);
    }
    if (c->ops->set_on) {
        c->ops->set_on(c->ctx, false);
    }
    if (c->ops->set_rgbw) {
        c->ops->set_rgbw(c->ctx, zero4, 1, 0, 0);
    }
}

/* ---------- driving outputs ---------- */

static void drive(int idx, const ul_state_msg_t *target, uint32_t fade_ms)
{
    const ul_output_def_t *d = &s_cfg.out[idx];
    out_rt_t *rt = &s_rt[idx];
    int n = 1;
    if (fade_ms >= 300) {
        n = (int)(fade_ms / 100);
        n = n > MAX_FADE_POINTS ? MAX_FADE_POINTS : n;
    }
    float duty[UL_ROLE_COUNT][MAX_FADE_POINTS];
    float rgbw[MAX_FADE_POINTS][4];
    for (int i = 0; i < n; i++) {
        ul_state_msg_t mid;
        ul_render_t r;
        ul_state_lerp(&rt->shown, target, (float)(i + 1) / (float)n, &mid);
        ul_render(d, &mid, &r);
        for (int role = 0; role < UL_ROLE_COUNT; role++) {
            duty[role][i] = r.v[role];
        }
        memcpy(rgbw[i], r.rgbw, sizeof(rgbw[i]));
    }
    for (int role = 0; role < UL_ROLE_COUNT; role++) {
        int ci = rt->ch[role];
        if (ci < 0) {
            continue;
        }
        const chan_t *c = &s_chans[ci];
        if (role == UL_ROLE_COIL && c->ops->set_on) {
            c->ops->set_on(c->ctx, duty[role][n - 1] > 0.5f);
        } else if (role == UL_ROLE_DATA && c->ops->set_rgbw) {
            c->ops->set_rgbw(c->ctx, (const float (*)[4])rgbw, n, d->pixels, fade_ms);
        } else if (c->ops->set_duty) {
            c->ops->set_duty(c->ctx, duty[role], n, fade_ms);
        }
    }
    rt->shown = *target;
}

static void bind_channels(int idx)
{
    const ul_output_def_t *d = &s_cfg.out[idx];
    for (int role = 0; role < UL_ROLE_COUNT; role++) {
        s_rt[idx].ch[role] = d->ch[role][0] ? find_chan(d->ch[role]) : -1;
        if (d->ch[role][0] && s_rt[idx].ch[role] < 0) {
            ul_core_report_error("channel_unavailable", d->key, "channel %s has no driver", d->ch[role]);
        }
    }
}

/* ---------- persistence ---------- */

typedef struct __attribute__((packed)) {
    char key[UL_KEY_SZ];
    uint8_t kind;
    uint8_t on;
    float level;
    uint16_t cct;
    uint8_t color[3];
    float white;
} saved_t;

static void save_states(void *arg)
{
    saved_t rec[UL_MAX_OUTPUTS];
    LOCK();
    int n = s_cfg.n;
    for (int i = 0; i < n; i++) {
        const ul_state_msg_t *c = &s_rt[i].st.cur;
        memset(&rec[i], 0, sizeof(rec[i]));
        memcpy(rec[i].key, s_cfg.out[i].key, sizeof(rec[i].key));
        rec[i].kind = (uint8_t)s_cfg.out[i].kind;
        rec[i].on = s_cfg.out[i].kind == UL_KIND_SOLENOID ? 0 : c->on;
        rec[i].level = c->level;
        rec[i].cct = c->cct;
        memcpy(rec[i].color, c->color, 3);
        rec[i].white = c->white;
    }
    UNLOCK();
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_blob(h, "states", rec, (size_t)n * sizeof(saved_t));
        nvs_commit(h);
        nvs_close(h);
    }
}

static void schedule_save(void)
{
    esp_timer_stop(s_save_timer);
    esp_timer_start_once(s_save_timer, SAVE_DELAY_US);
}

static void restore_states(void)
{
    saved_t rec[UL_MAX_OUTPUTS];
    size_t len = sizeof(rec);
    int n = 0;
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        if (nvs_get_blob(h, "states", rec, &len) == ESP_OK) {
            n = (int)(len / sizeof(saved_t));
        }
        nvs_close(h);
    }
    for (int i = 0; i < s_cfg.n; i++) {
        const ul_output_def_t *d = &s_cfg.out[i];
        ul_state_init(&s_rt[i].st, d);
        s_rt[i].shown = s_rt[i].st.cur;
        s_rt[i].shown.on = false;
        ul_state_msg_t *c = &s_rt[i].st.cur;
        for (int k = 0; k < n; k++) {
            if (strncmp(rec[k].key, d->key, UL_KEY_SZ) == 0 && rec[k].kind == d->kind) {
                c->on = rec[k].on;
                c->level = rec[k].level;
                c->cct = rec[k].cct;
                memcpy(c->color, rec[k].color, 3);
                c->white = rec[k].white;
                break;
            }
        }
        if (d->power_on == UL_POWER_ON_OFF || d->kind == UL_KIND_SOLENOID) {
            c->on = false;
        } else if (d->power_on == UL_POWER_ON_ON) {
            c->on = true;
        }
        snprintf(c->src, sizeof(c->src), "device");
    }
}

static void save_config(void)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) != ESP_OK) {
        return;
    }
    if (s_cfg.rev == 0) {
        nvs_erase_key(h, "config");
    } else {
        char *json = ul_config_encode(&s_cfg);
        if (json) {
            nvs_set_str(h, "config", json);
            free(json);
        }
    }
    nvs_commit(h);
    nvs_close(h);
}

static void load_config(void)
{
    ul_mapping_default(s_board, &s_cfg);
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        return;
    }
    size_t len = 0;
    if (nvs_get_str(h, "config", NULL, &len) == ESP_OK && len > 1) {
        char *json = malloc(len);
        ul_config_t *c = malloc(sizeof(*c));
        if (json && c && nvs_get_str(h, "config", json, &len) == ESP_OK) {
            char err[120];
            if (ul_config_decode(json, len - 1, c, err, sizeof(err)) == 0 &&
                ul_mapping_validate(s_board, c, err, sizeof(err)) == 0) {
                s_cfg = *c;
            } else {
                snprintf(s_cfg_err, sizeof(s_cfg_err), "saved config ignored: %s", err);
                ESP_LOGW(TAG, "%s", s_cfg_err);
            }
        }
        free(json);
        free(c);
    }
    nvs_close(h);
}

/* ---------- timers: pulses and identify ---------- */

static void pulse_tick(void *arg)
{
    int64_t now = esp_timer_get_time();
    int64_t next = 0;
    int ended[UL_MAX_OUTPUTS], n_ended = 0;
    LOCK();
    for (int i = 0; i < s_cfg.n; i++) {
        out_rt_t *rt = &s_rt[i];
        if (!rt->pulse_end_us) {
            continue;
        }
        if (rt->pulse_end_us <= now) {
            rt->pulse_end_us = 0;
            rt->st.cur.on = false;
            rt->st.cur.fields &= ~(uint32_t)UL_F_PULSE_LEFT;
            drive(i, &rt->st.cur, 0);
            ended[n_ended++] = i;
        } else if (!next || rt->pulse_end_us < next) {
            next = rt->pulse_end_us;
        }
    }
    if (next) {
        esp_timer_start_once(s_pulse_timer, (uint64_t)(next - now));
    }
    UNLOCK();
    for (int i = 0; i < n_ended; i++) {
        post_state(ended[i]);
    }
}

static void identify_tick(void *arg)
{
    LOCK();
    bool done = esp_timer_get_time() >= s_identify_end_us;
    s_identify_phase = !s_identify_phase;
    for (int i = 0; i < s_cfg.n; i++) {
        if (s_identify_idx != -1 && s_identify_idx != i) {
            continue;
        }
        ul_kind_t k = s_cfg.out[i].kind;
        if (k == UL_KIND_RELAY || k == UL_KIND_SOLENOID || k == UL_KIND_MOTION) {
            continue; /* identify blinks lights only: never cycle relays or valves */
        }
        if (done) {
            drive(i, &s_rt[i].st.cur, 0);
            continue;
        }
        ul_state_msg_t blink = s_rt[i].st.cur;
        blink.on = s_identify_phase;
        blink.level = 1.0f;
        drive(i, &blink, 0);
    }
    if (done) {
        esp_timer_stop(s_identify_timer);
        s_identify_idx = -2;
    }
    UNLOCK();
}

/* ---------- public API ---------- */

int ul_core_output_count(void)
{
    return s_cfg.n;
}

int ul_core_find_output(const char *key)
{
    for (int i = 0; i < s_cfg.n; i++) {
        if (strcmp(s_cfg.out[i].key, key) == 0) {
            return i;
        }
    }
    return -1;
}

bool ul_core_output_def(int idx, ul_output_def_t *out)
{
    LOCK();
    bool ok = idx >= 0 && idx < s_cfg.n;
    if (ok) {
        *out = s_cfg.out[idx];
    }
    UNLOCK();
    return ok;
}

bool ul_core_output_state(int idx, ul_state_msg_t *out)
{
    LOCK();
    bool ok = idx >= 0 && idx < s_cfg.n;
    if (ok) {
        *out = s_rt[idx].st.cur;
        if (s_rt[idx].pulse_end_us) {
            int64_t left = (s_rt[idx].pulse_end_us - esp_timer_get_time()) / 1000;
            out->pulse_ms_left = left > 0 ? (uint32_t)left : 0;
            out->fields |= UL_F_PULSE_LEFT;
        }
        out->fields &= ~(uint32_t)UL_F_FADE;
    }
    UNLOCK();
    return ok;
}

void ul_core_config(ul_config_t *out)
{
    LOCK();
    *out = s_cfg;
    UNLOCK();
}

int ul_core_config_rev(void)
{
    return s_cfg.rev;
}

const char *ul_core_config_error(void)
{
    if (!s_board && ul_board_error()) {
        return ul_board_error();
    }
    return s_cfg_err[0] ? s_cfg_err : NULL;
}

esp_err_t ul_core_apply(const char *key, const ul_state_msg_t *msg, bool live)
{
    LOCK();
    int idx = ul_core_find_output(key);
    if (idx < 0) {
        UNLOCK();
        return ESP_ERR_NOT_FOUND;
    }
    const ul_output_def_t *d = &s_cfg.out[idx];
    if (d->kind == UL_KIND_SOLENOID || d->kind == UL_KIND_MOTION) {
        UNLOCK();
        return ESP_ERR_NOT_SUPPORTED; /* solenoids only pulse; motion is an input */
    }
    if (!ul_state_apply(&s_rt[idx].st, d, msg, live)) {
        UNLOCK();
        return ESP_ERR_INVALID_STATE;
    }
    uint32_t fade = (msg->fields & UL_F_FADE) ? msg->fade_ms : (live ? 60 : 400);
    if (s_identify_idx == -2) {
        drive(idx, &s_rt[idx].st.cur, fade);
    }
    schedule_save();
    UNLOCK();
    post_state(idx);
    return ESP_OK;
}

esp_err_t ul_core_apply_config(const char *json, size_t len)
{
    if (!s_board) {
        return ESP_ERR_INVALID_STATE;
    }
    ul_config_t *c = malloc(sizeof(*c));
    if (!c) {
        return ESP_ERR_NO_MEM;
    }
    char err[120];
    esp_err_t rc = ESP_OK;
    if (len == 0) {
        ul_mapping_default(s_board, c);
    } else if (ul_config_decode(json, len, c, err, sizeof(err)) != 0 ||
               ul_mapping_validate(s_board, c, err, sizeof(err)) != 0) {
        LOCK();
        snprintf(s_cfg_err, sizeof(s_cfg_err), "config rev rejected: %s", err);
        UNLOCK();
        ul_core_report_error("config_rejected", NULL, "%s", err);
        esp_event_post(UL_EVENT, UL_EV_INFO, NULL, 0, 0);
        free(c);
        return ESP_ERR_INVALID_ARG;
    }

    LOCK();
    if (c->rev == s_cfg.rev && len != 0 && s_cfg_err[0] == '\0') {
        UNLOCK();
        free(c);
        return ESP_OK; /* retained copy of what we already run */
    }
    /* Keep the state of outputs that survive with the same key and kind. */
    static out_rt_t old_rt[UL_MAX_OUTPUTS];
    static ul_config_t old_cfg;
    memcpy(old_rt, s_rt, sizeof(old_rt));
    old_cfg = s_cfg;
    s_cfg = *c;
    s_cfg_err[0] = '\0';
    bool used[UL_MAX_CHANNELS] = {0};
    for (int i = 0; i < s_cfg.n; i++) {
        const ul_output_def_t *d = &s_cfg.out[i];
        memset(&s_rt[i], 0, sizeof(s_rt[i]));
        ul_state_init(&s_rt[i].st, d);
        s_rt[i].shown = s_rt[i].st.cur;
        s_rt[i].shown.on = false;
        for (int k = 0; k < old_cfg.n; k++) {
            if (strcmp(old_cfg.out[k].key, d->key) == 0 && old_cfg.out[k].kind == d->kind) {
                s_rt[i].st = old_rt[k].st;
                s_rt[i].shown = old_rt[k].shown;
                break;
            }
        }
        bind_channels(i);
        for (int r = 0; r < UL_ROLE_COUNT; r++) {
            if (s_rt[i].ch[r] >= 0) {
                used[s_rt[i].ch[r]] = true;
            }
        }
    }
    for (int ci = 0; ci < s_nchan; ci++) {
        if (!used[ci]) {
            chan_off(ci);
        }
    }
    for (int i = 0; i < s_cfg.n; i++) {
        drive(i, &s_rt[i].st.cur, 0);
    }
    save_config();
    schedule_save();
    int n = s_cfg.n;
    UNLOCK();
    free(c);
    ESP_LOGI(TAG, "config rev %d applied, %d outputs", s_cfg.rev, n);
    esp_event_post(UL_EVENT, UL_EV_INFO, NULL, 0, 0);
    for (int i = 0; i < n; i++) {
        post_state(i);
    }
    return rc;
}

esp_err_t ul_core_pulse(const char *key, uint32_t ms)
{
    LOCK();
    int idx = ul_core_find_output(key);
    if (idx < 0 || s_cfg.out[idx].kind != UL_KIND_SOLENOID) {
        UNLOCK();
        return idx < 0 ? ESP_ERR_NOT_FOUND : ESP_ERR_NOT_SUPPORTED;
    }
    uint32_t max = s_cfg.out[idx].max_on_ms;
    if (ms > max) {
        ms = max;
    }
    out_rt_t *rt = &s_rt[idx];
    rt->st.cur.on = true;
    snprintf(rt->st.cur.src, sizeof(rt->st.cur.src), "device");
    rt->pulse_end_us = esp_timer_get_time() + (int64_t)ms * 1000;
    drive(idx, &rt->st.cur, 0);
    esp_timer_stop(s_pulse_timer);
    UNLOCK();
    pulse_tick(NULL); /* reschedules for the earliest end */
    post_state(idx);
    return ESP_OK;
}

esp_err_t ul_core_close(const char *key)
{
    LOCK();
    int idx = ul_core_find_output(key);
    if (idx < 0 || s_cfg.out[idx].kind != UL_KIND_SOLENOID) {
        UNLOCK();
        return idx < 0 ? ESP_ERR_NOT_FOUND : ESP_ERR_NOT_SUPPORTED;
    }
    bool running = s_rt[idx].pulse_end_us != 0;
    if (running) {
        s_rt[idx].pulse_end_us = esp_timer_get_time(); /* ends now: pulse_tick drives it off */
        esp_timer_stop(s_pulse_timer);
    }
    UNLOCK();
    if (running) {
        pulse_tick(NULL); /* closes it, posts state, reschedules for the other valves */
    }
    return ESP_OK;
}

esp_err_t ul_core_identify(const char *key, uint32_t ms)
{
    LOCK();
    int idx = -1;
    if (key && key[0]) {
        idx = ul_core_find_output(key);
        if (idx < 0) {
            UNLOCK();
            return ESP_ERR_NOT_FOUND;
        }
    }
    s_identify_idx = idx;
    s_identify_end_us = esp_timer_get_time() + (int64_t)ms * 1000;
    s_identify_phase = false;
    esp_timer_stop(s_identify_timer);
    esp_timer_start_periodic(s_identify_timer, IDENTIFY_TICK_US);
    UNLOCK();
    return ESP_OK;
}

esp_err_t ul_core_forget(void)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err == ESP_OK) {
        nvs_erase_all(h);
        err = nvs_commit(h);
        nvs_close(h);
    }
    return err;
}

/* ---------- boot ---------- */

static esp_err_t init_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS unreadable (%s), erasing", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

esp_err_t ul_core_boot(void)
{
    ESP_ERROR_CHECK(init_nvs());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_lock = xSemaphoreCreateRecursiveMutex();

    const esp_timer_create_args_t save = {.callback = save_states, .name = "ul_save"};
    const esp_timer_create_args_t pulse = {.callback = pulse_tick, .name = "ul_pulse"};
    const esp_timer_create_args_t ident = {.callback = identify_tick, .name = "ul_identify"};
    ESP_ERROR_CHECK(esp_timer_create(&save, &s_save_timer));
    ESP_ERROR_CHECK(esp_timer_create(&pulse, &s_pulse_timer));
    ESP_ERROR_CHECK(esp_timer_create(&ident, &s_identify_timer));

    qsort(s_modules, (size_t)s_nmodules, sizeof(s_modules[0]), module_cmp);

    ul_board_load();
    s_board = ul_board_get();
    if (s_board) {
        run_stage(UL_STAGE_DRIVER);
        LOCK();
        load_config();
        restore_states();
        for (int i = 0; i < s_cfg.n; i++) {
            bind_channels(i);
            drive(i, &s_rt[i].st.cur, s_cfg.out[i].kind == UL_KIND_RELAY ? 0 : 300);
        }
        UNLOCK();
        ESP_LOGI(TAG, "%d channels, %d outputs (config rev %d)", s_nchan, s_cfg.n, s_cfg.rev);
    } else {
        ESP_LOGW(TAG, "no usable board descriptor; outputs stay off");
    }
    run_stage(UL_STAGE_SERVICE);
    return ESP_OK;
}

/* MQTT and enrollment messages. */
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "ul_json.h"

int ul__kind_from_name(const char *s);
int ul__role_from_name(const char *s);
int ul__op_from_name(const char *s);
cJSON *ul_channel_to_cjson(const ul_channel_desc_t *ch);

#define MAX_SEQ 9007199254740991LL

static const char *const CAP_NAMES[] = {"onoff", "level", "cct", "color", "white",
                                        "effect", "pulse", "motion", "power", "mode"};
static const char *const CURVE_NAMES[] = {"cie", "linear"};
static const char *const POWER_ON_NAMES[] = {"last", "off", "on"};

static bool is_hex(const char *s, size_t n)
{
    if (strlen(s) != n) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) {
            return false;
        }
    }
    return true;
}

static bool is_sha256(const char *s) { return is_hex(s, 64); }
static bool is_name(const char *s) { return s[0] != '\0'; }
static bool is_mode(const char *s) { return ul_is_key(s) && strcmp(s, "off") != 0; }
static bool is_at(const char *s) { return ul_is_key(s); }

/* ---------- outputs and config ---------- */

static int output_from_cjson(const cJSON *o, ul_output_def_t *d, jerr_t *e)
{
    if (!cJSON_IsObject(o)) {
        return j_fail(e, "output must be an object");
    }
    memset(d, 0, sizeof(*d));
    d->cct_warm = 2700;
    d->cct_cool = 6500;
    d->max_level = 1.0f;
    char kind[12], s[8];
    long long v;
    double f;
    if (j_str(o, "key", d->key, sizeof(d->key), true, ul_is_key, e) < 0 ||
        j_str(o, "kind", kind, sizeof(kind), true, NULL, e) < 0 ||
        j_str(o, "name", d->name, sizeof(d->name), false, is_name, e) < 0) {
        return J_BAD;
    }
    int k = ul__kind_from_name(kind);
    if (k < 0) {
        return j_fail(e, "output %s: unknown kind '%s'", d->key, kind);
    }
    d->kind = (ul_kind_t)k;
    const cJSON *chs = cJSON_GetObjectItemCaseSensitive(o, "channels");
    if (!cJSON_IsObject(chs) || !chs->child) {
        return j_fail(e, "output %s: 'channels' must be a non-empty object", d->key);
    }
    for (const cJSON *c = chs->child; c; c = c->next) {
        int r = ul__role_from_name(c->string);
        if (r < 0) {
            return j_fail(e, "output %s: unknown role '%s'", d->key, c->string);
        }
        if (!cJSON_IsString(c) || !ul_is_key(c->valuestring)) {
            return j_fail(e, "output %s: role %s needs a channel id", d->key, c->string);
        }
        strcpy(d->ch[r], c->valuestring);
    }
    const cJSON *cr = cJSON_GetObjectItemCaseSensitive(o, "cct_range");
    if (cr) {
        if (!cJSON_IsArray(cr) || cJSON_GetArraySize(cr) != 2) {
            return j_fail(e, "output %s: cct_range must be [warm, cool]", d->key);
        }
        for (int i = 0; i < 2; i++) {
            const cJSON *x = cJSON_GetArrayItem(cr, i);
            if (!cJSON_IsNumber(x) || x->valuedouble != floor(x->valuedouble) ||
                x->valuedouble < 1000 || x->valuedouble > 10000) {
                return j_fail(e, "output %s: bad cct_range", d->key);
            }
        }
        d->cct_warm = (uint16_t)cJSON_GetArrayItem(cr, 0)->valuedouble;
        d->cct_cool = (uint16_t)cJSON_GetArrayItem(cr, 1)->valuedouble;
    }
    int r = j_str(o, "curve", s, sizeof(s), false, NULL, e);
    if (r < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        if (strcmp(s, "cie") == 0) {
            d->curve = UL_CURVE_CIE;
        } else if (strcmp(s, "linear") == 0) {
            d->curve = UL_CURVE_LINEAR;
        } else {
            return j_fail(e, "output %s: unknown curve", d->key);
        }
    }
    if ((r = j_num(o, "min_duty", 0, 0.5, &f, false, e)) < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        d->min_duty = (float)f;
    }
    if ((r = j_num(o, "max_level", 0.05, 1, &f, false, e)) < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        d->max_level = (float)f;
    }
    if ((r = j_int(o, "pixels", 1, UL_MAX_PIXELS, &v, false, e)) < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        d->pixels = (uint16_t)v;
    }
    if ((r = j_str(o, "power_on", s, sizeof(s), false, NULL, e)) < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        int i;
        for (i = 0; i < 3 && strcmp(s, POWER_ON_NAMES[i]) != 0; i++) {
        }
        if (i == 3) {
            return j_fail(e, "output %s: unknown power_on", d->key);
        }
        d->power_on = (ul_power_on_t)i;
    }
    if ((r = j_int(o, "max_on_ms", 50, UL_MAX_PULSE_MS, &v, d->kind == UL_KIND_SOLENOID, e)) < 0) {
        return J_BAD;
    }
    if (r == J_OK) {
        d->max_on_ms = (uint32_t)v;
    }
    /* caps are device-reported; validate their shape but don't trust them. */
    const cJSON *caps = cJSON_GetObjectItemCaseSensitive(o, "caps");
    if (caps && !cJSON_IsArray(caps)) {
        return j_fail(e, "output %s: caps must be an array", d->key);
    }
    return J_OK;
}

static cJSON *output_to_cjson(const ul_output_def_t *d, bool with_caps)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "key", d->key);
    cJSON_AddStringToObject(o, "kind", ul_kind_name(d->kind));
    if (d->name[0]) {
        cJSON_AddStringToObject(o, "name", d->name);
    }
    cJSON *chs = cJSON_AddObjectToObject(o, "channels");
    for (int r = 0; r < UL_ROLE_COUNT; r++) {
        if (d->ch[r][0]) {
            cJSON_AddStringToObject(chs, ul_role_name((ul_role_t)r), d->ch[r]);
        }
    }
    if (with_caps) {
        cJSON *caps = cJSON_AddArrayToObject(o, "caps");
        for (int i = 0; i < (int)(sizeof(CAP_NAMES) / sizeof(CAP_NAMES[0])); i++) {
            if (d->caps & (1u << i)) {
                cJSON_AddItemToArray(caps, cJSON_CreateString(CAP_NAMES[i]));
            }
        }
    }
    if (d->kind == UL_KIND_CCT || d->kind == UL_KIND_RGBCCT) {
        cJSON *cr = cJSON_AddArrayToObject(o, "cct_range");
        cJSON_AddItemToArray(cr, cJSON_CreateNumber(d->cct_warm));
        cJSON_AddItemToArray(cr, cJSON_CreateNumber(d->cct_cool));
    }
    if (d->curve != UL_CURVE_CIE) {
        cJSON_AddStringToObject(o, "curve", CURVE_NAMES[d->curve]);
    }
    if (d->min_duty > 0) {
        cJSON_AddNumberToObject(o, "min_duty", d->min_duty);
    }
    if (d->max_level < 1.0f) {
        cJSON_AddNumberToObject(o, "max_level", d->max_level);
    }
    if (d->kind == UL_KIND_ARGB && d->pixels) {
        cJSON_AddNumberToObject(o, "pixels", d->pixels);
    }
    if (d->power_on != UL_POWER_ON_LAST) {
        cJSON_AddStringToObject(o, "power_on", POWER_ON_NAMES[d->power_on]);
    }
    if (d->kind == UL_KIND_SOLENOID) {
        cJSON_AddNumberToObject(o, "max_on_ms", d->max_on_ms);
    }
    return o;
}

int ul_config_decode(const char *json, size_t len, ul_config_t *c, char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    cJSON *root = j_parse_object(json, len, &e);
    if (!root) {
        return J_BAD;
    }
    int rc = J_BAD;
    long long v;
    memset(c, 0, sizeof(*c));
    if (j_int(root, "v", 1, 1, &v, true, &e) < 0 || j_int(root, "rev", 1, MAX_SEQ, &v, true, &e) < 0) {
        goto out;
    }
    c->rev = (int)v;
    const cJSON *outs = cJSON_GetObjectItemCaseSensitive(root, "outputs");
    if (!cJSON_IsArray(outs)) {
        j_fail(&e, "missing 'outputs' array");
        goto out;
    }
    if (cJSON_GetArraySize(outs) > UL_MAX_OUTPUTS) {
        j_fail(&e, "too many outputs");
        goto out;
    }
    const cJSON *o;
    cJSON_ArrayForEach(o, outs) {
        if (output_from_cjson(o, &c->out[c->n], &e) < 0) {
            goto out;
        }
        c->n++;
    }
    rc = 0;
out:
    cJSON_Delete(root);
    return rc;
}

char *ul_config_encode(const ul_config_t *c)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddNumberToObject(j, "v", 1);
    cJSON_AddNumberToObject(j, "rev", c->rev);
    cJSON *outs = cJSON_AddArrayToObject(j, "outputs");
    for (int i = 0; i < c->n; i++) {
        cJSON_AddItemToArray(outs, output_to_cjson(&c->out[i], false));
    }
    return j_print(j);
}

/* ---------- set / live / state ---------- */

typedef enum { M_SET, M_LIVE, M_STATE } state_mode_t;

static int state_decode(const char *json, size_t len, ul_state_msg_t *m, state_mode_t mode,
                        char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    cJSON *root = j_parse_object(json, len, &e);
    if (!root) {
        return J_BAD;
    }
    int rc = J_BAD, r;
    long long v;
    double f;
    memset(m, 0, sizeof(*m));
    bool req_on = (mode != M_LIVE);

    if ((r = j_bool(root, "on", &m->on, req_on, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->fields |= UL_F_ON;
    }
    if ((r = j_num(root, "level", 0, 1, &f, false, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->level = (float)f;
        m->fields |= UL_F_LEVEL;
    }
    if ((r = j_int(root, "cct", 1000, 10000, &v, false, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->cct = (uint16_t)v;
        m->fields |= UL_F_CCT;
    }
    const cJSON *col = cJSON_GetObjectItemCaseSensitive(root, "color");
    if (col) {
        if (!cJSON_IsArray(col) || cJSON_GetArraySize(col) != 3) {
            j_fail(&e, "'color' must be [r, g, b]");
            goto out;
        }
        for (int i = 0; i < 3; i++) {
            const cJSON *x = cJSON_GetArrayItem(col, i);
            if (!cJSON_IsNumber(x) || x->valuedouble != floor(x->valuedouble) ||
                x->valuedouble < 0 || x->valuedouble > 255) {
                j_fail(&e, "'color' values must be 0-255");
                goto out;
            }
            m->color[i] = (uint8_t)x->valuedouble;
        }
        m->fields |= UL_F_COLOR;
    }
    if ((r = j_num(root, "white", 0, 1, &f, false, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->white = (float)f;
        m->fields |= UL_F_WHITE;
    }
    const cJSON *fx = cJSON_GetObjectItemCaseSensitive(root, "effect");
    if (fx && mode != M_LIVE) {
        if (cJSON_IsNull(fx)) {
            m->effect[0] = '\0';
        } else if (cJSON_IsObject(fx)) {
            if (j_str(fx, "name", m->effect, sizeof(m->effect), true, ul_is_key, &e) < 0) {
                goto out;
            }
        } else {
            j_fail(&e, "'effect' must be an object or null");
            goto out;
        }
        m->fields |= UL_F_EFFECT;
    }
    if (mode != M_LIVE) {
        if ((r = j_str(root, "mode", m->mode, sizeof(m->mode), false, is_mode, &e)) < 0) {
            goto out;
        }
        if (r == J_OK) {
            m->fields |= UL_F_MODE;
        }
    }
    if ((r = j_int(root, "fade_ms", 0, 60000, &v, false, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->fade_ms = (uint32_t)v;
        m->fields |= UL_F_FADE;
    }
    if ((r = j_str(root, "src", m->src, sizeof(m->src), false, ul_is_src, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        m->fields |= UL_F_SRC;
    }
    if (j_int(root, "seq", 0, MAX_SEQ, &v, true, &e) < 0) {
        goto out;
    }
    m->seq = (uint64_t)v;
    m->fields |= UL_F_SEQ;
    if (mode == M_STATE) {
        if ((r = j_int(root, "pulse_ms_left", 0, 0x7fffffff, &v, false, &e)) < 0) {
            goto out;
        }
        if (r == J_OK) {
            m->pulse_ms_left = (uint32_t)v;
            m->fields |= UL_F_PULSE_LEFT;
        }
        if ((r = j_bool(root, "motion", &m->motion, false, &e)) < 0) {
            goto out;
        }
        if (r == J_OK) {
            m->fields |= UL_F_MOTION;
        }
        if ((r = j_str(root, "at", m->at, sizeof(m->at), false, is_at, &e)) < 0) {
            goto out;
        }
        if (r == J_OK) {
            m->fields |= UL_F_AT;
        }
    }
    if (mode == M_LIVE && !(m->fields & (UL_F_ON | UL_F_LEVEL | UL_F_CCT | UL_F_COLOR | UL_F_WHITE | UL_F_FADE | UL_F_SRC))) {
        j_fail(&e, "live message changes nothing");
        goto out;
    }
    rc = 0;
out:
    cJSON_Delete(root);
    return rc;
}

int ul_set_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen)
{
    return state_decode(json, len, out, M_SET, err, errlen);
}

int ul_live_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen)
{
    return state_decode(json, len, out, M_LIVE, err, errlen);
}

int ul_state_decode(const char *json, size_t len, ul_state_msg_t *out, char *err, size_t errlen)
{
    return state_decode(json, len, out, M_STATE, err, errlen);
}

char *ul_state_encode(const ul_state_msg_t *m)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddBoolToObject(j, "on", m->on);
    if (m->fields & UL_F_LEVEL) {
        /* Round to 4 places so 0.42f prints as 0.42, not 0.41999998688697815. */
        cJSON_AddNumberToObject(j, "level", round(m->level * 10000.0) / 10000.0);
    }
    if (m->fields & UL_F_CCT) {
        cJSON_AddNumberToObject(j, "cct", m->cct);
    }
    if (m->fields & UL_F_COLOR) {
        cJSON *c = cJSON_AddArrayToObject(j, "color");
        for (int i = 0; i < 3; i++) {
            cJSON_AddItemToArray(c, cJSON_CreateNumber(m->color[i]));
        }
    }
    if (m->fields & UL_F_WHITE) {
        cJSON_AddNumberToObject(j, "white", round(m->white * 10000.0) / 10000.0);
    }
    if (m->fields & UL_F_EFFECT) {
        if (m->effect[0]) {
            cJSON *fx = cJSON_AddObjectToObject(j, "effect");
            cJSON_AddStringToObject(fx, "name", m->effect);
        } else {
            cJSON_AddNullToObject(j, "effect");
        }
    }
    if (m->fields & UL_F_PULSE_LEFT) {
        cJSON_AddNumberToObject(j, "pulse_ms_left", m->pulse_ms_left);
    }
    if (m->fields & UL_F_MOTION) {
        cJSON_AddBoolToObject(j, "motion", m->motion);
    }
    if ((m->fields & UL_F_MODE) && m->mode[0]) {
        cJSON_AddStringToObject(j, "mode", m->mode);
    }
    if ((m->fields & UL_F_AT) && m->at[0]) {
        cJSON_AddStringToObject(j, "at", m->at);
    }
    if (m->src[0]) {
        cJSON_AddStringToObject(j, "src", m->src);
    }
    /* cJSON would print 16-digit numbers as 1.76360000000001e+15 (and can
     * round near 2^53), so seq is written as integer text. */
    char seq[24];
    snprintf(seq, sizeof(seq), "%llu", (unsigned long long)m->seq);
    cJSON_AddRawToObject(j, "seq", seq);
    return j_print(j);
}

char *ul_status_encode(bool online)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddBoolToObject(j, "online", online);
    return j_print(j);
}

/* ---------- cmd ---------- */

static bool is_cmd_id(const char *s)
{
    size_t n = strlen(s);
    if (n < 8 || n > 40) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-')) {
            return false;
        }
    }
    return true;
}

static bool is_https(const char *s) { return strncmp(s, "https://", 8) == 0; }

int ul_cmd_decode(const char *json, size_t len, ul_cmd_t *c, char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    cJSON *root = j_parse_object(json, len, &e);
    if (!root) {
        return J_BAD;
    }
    int rc = J_BAD, r;
    long long v;
    char op[16];
    memset(c, 0, sizeof(*c));
    if (j_str(root, "id", c->id, sizeof(c->id), true, is_cmd_id, &e) < 0 ||
        j_str(root, "op", op, sizeof(op), true, NULL, &e) < 0 ||
        j_str(root, "src", c->src, sizeof(c->src), false, ul_is_src, &e) < 0) {
        goto out;
    }
    int o = ul__op_from_name(op);
    if (o < 0) {
        j_fail(&e, "unknown op '%s'", op);
        goto out;
    }
    c->op = (ul_op_t)o;
    if ((r = j_int(root, "ts", 0, MAX_SEQ, &v, false, &e)) < 0) {
        goto out;
    }
    if (r == J_OK) {
        c->ts = v;
    }
    switch (c->op) {
    case UL_OP_IDENTIFY:
        c->ms = 5000;
        if ((r = j_int(root, "ms", 500, 60000, &v, false, &e)) < 0) {
            goto out;
        }
        if (r == J_OK) {
            c->ms = (uint32_t)v;
        }
        if (j_str(root, "out", c->out, sizeof(c->out), false, ul_is_key, &e) < 0) {
            goto out;
        }
        break;
    case UL_OP_FACTORY_RESET: {
        bool confirm = false;
        if (j_bool(root, "confirm", &confirm, true, &e) < 0) {
            goto out;
        }
        if (!confirm) {
            j_fail(&e, "factory_reset needs confirm: true");
            goto out;
        }
        break;
    }
    case UL_OP_PULSE:
        if (j_str(root, "out", c->out, sizeof(c->out), true, ul_is_key, &e) < 0 ||
            j_int(root, "ms", 10, UL_MAX_PULSE_MS, &v, true, &e) < 0) {
            goto out;
        }
        c->ms = (uint32_t)v;
        break;
    case UL_OP_CLOSE:
        if (j_str(root, "out", c->out, sizeof(c->out), true, ul_is_key, &e) < 0) {
            goto out;
        }
        break;
    case UL_OP_SYNC:
        if (j_str(root, "out", c->out, sizeof(c->out), true, ul_is_key, &e) < 0 ||
            j_str(root, "at", c->at, sizeof(c->at), true, is_at, &e) < 0) {
            goto out;
        }
        break;
    case UL_OP_OTA:
        if (j_str(root, "url", c->url, sizeof(c->url), true, is_https, &e) < 0 ||
            j_str(root, "sha256", c->sha256, sizeof(c->sha256), true, is_sha256, &e) < 0 ||
            j_str(root, "version", c->version, sizeof(c->version), true, ul_is_semver, &e) < 0) {
            goto out;
        }
        break;
    default:
        break;
    }
    rc = 0;
out:
    cJSON_Delete(root);
    return rc;
}

/* ---------- evt / health / info ---------- */

char *ul_evt_encode(const ul_evt_t *ev)
{
    static const char *const types[] = {"cmd_result", "button", "error", "motion"};
    static const char *const actions[] = {"press", "hold", "reset"};
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "type", types[ev->type]);
    switch (ev->type) {
    case UL_EVT_CMD_RESULT:
        cJSON_AddStringToObject(j, "cmd_id", ev->cmd_id);
        cJSON_AddBoolToObject(j, "ok", ev->ok);
        if (ev->error) {
            cJSON_AddStringToObject(j, "error", ev->error);
        }
        if (ev->detail) {
            cJSON_AddStringToObject(j, "detail", ev->detail);
        }
        if (ev->progress >= 0) {
            cJSON_AddNumberToObject(j, "progress", ev->progress);
        }
        break;
    case UL_EVT_BUTTON:
        cJSON_AddStringToObject(j, "action", actions[ev->action]);
        break;
    case UL_EVT_ERROR:
        cJSON_AddStringToObject(j, "code", ev->code ? ev->code : "unknown");
        if (ev->detail) {
            cJSON_AddStringToObject(j, "detail", ev->detail);
        }
        if (ev->out) {
            cJSON_AddStringToObject(j, "out", ev->out);
        }
        break;
    case UL_EVT_MOTION:
        cJSON_AddStringToObject(j, "out", ev->out ? ev->out : "");
        cJSON_AddBoolToObject(j, "active", ev->active);
        break;
    }
    return j_print(j);
}

char *ul_health_encode(const ul_health_t *h)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddNumberToObject(j, "uptime_s", h->uptime_s);
    cJSON_AddNumberToObject(j, "heap_free", h->heap_free);
    cJSON_AddNumberToObject(j, "heap_min", h->heap_min);
    cJSON_AddNumberToObject(j, "rssi", h->rssi);
    cJSON_AddStringToObject(j, "ip", h->ip);
    if (h->channel > 0) {
        cJSON_AddNumberToObject(j, "channel", h->channel);
    }
    cJSON_AddStringToObject(j, "reset_reason", h->reset_reason ? h->reset_reason : "unknown");
    cJSON_AddNumberToObject(j, "wifi_reconnects", h->wifi_reconnects);
    cJSON_AddNumberToObject(j, "mqtt_reconnects", h->mqtt_reconnects);
    if (h->has_temp) {
        cJSON_AddNumberToObject(j, "temp_c", round(h->temp_c * 10.0) / 10.0);
    }
    if (h->ota) {
        cJSON_AddStringToObject(j, "ota", h->ota);
    }
    if (h->dns[0]) {
        cJSON_AddStringToObject(j, "dns", h->dns);
    }
    return j_print(j);
}

static void add_hex32(cJSON *j, const char *k, uint32_t v)
{
    char s[9];
    snprintf(s, sizeof(s), "%08x", (unsigned)v);
    cJSON_AddStringToObject(j, k, s);
}

static void add_mac(cJSON *j, const uint8_t mac[6])
{
    char s[18];
    ul_mac_format(mac, s);
    cJSON_AddStringToObject(j, "mac", s);
}

char *ul_info_encode(const ul_info_t *in)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddNumberToObject(j, "v", 1);
    cJSON_AddStringToObject(j, "id", in->id);
    cJSON_AddStringToObject(j, "chip", ul_chip_name(in->chip));
    cJSON_AddStringToObject(j, "fw", in->fw);
    cJSON_AddStringToObject(j, "profile", in->profile);
    if (in->idf) {
        cJSON_AddStringToObject(j, "idf", in->idf);
    }
    cJSON_AddStringToObject(j, "board", in->board->board);
    if (in->board->hw_rev[0]) {
        cJSON_AddStringToObject(j, "hw_rev", in->board->hw_rev);
    }
    add_hex32(j, "board_crc", in->board_crc);
    add_mac(j, in->mac);
    cJSON *chs = cJSON_AddArrayToObject(j, "channels");
    for (int i = 0; i < in->board->n_channels; i++) {
        cJSON_AddItemToArray(chs, ul_channel_to_cjson(&in->board->ch[i]));
    }
    cJSON *outs = cJSON_AddArrayToObject(j, "outputs");
    for (int i = 0; i < in->outputs->n; i++) {
        const ul_output_def_t *d = &in->outputs->out[i];
        cJSON *o = output_to_cjson(d, true);
        /* cycle: the press channel's modes, so the server and UI need no board lookup. */
        for (int k = 0; d->kind == UL_KIND_CYCLE && k < in->board->n_channels; k++) {
            const ul_channel_desc_t *ch = &in->board->ch[k];
            if (strcmp(ch->id, d->ch[UL_ROLE_PRESS]) == 0) {
                cJSON *m = cJSON_AddArrayToObject(o, "modes");
                for (int x = 0; x < ch->n_modes; x++) {
                    cJSON_AddItemToArray(m, cJSON_CreateString(ch->modes[x]));
                }
            }
        }
        cJSON_AddItemToArray(outs, o);
    }
    cJSON_AddNumberToObject(j, "config_rev", in->config_rev);
    if (in->config_error) {
        cJSON_AddStringToObject(j, "config_error", in->config_error);
    }
    return j_print(j);
}

/* ---------- enrollment ---------- */

char *ul_enroll_req_encode(const ul_enroll_req_t *r)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "device_id", r->device_id);
    cJSON_AddStringToObject(j, "token", r->token);
    cJSON_AddStringToObject(j, "csr", r->csr);
    cJSON_AddStringToObject(j, "chip", ul_chip_name(r->chip));
    add_mac(j, r->mac);
    cJSON_AddStringToObject(j, "fw", r->fw);
    if (r->profile) {
        cJSON_AddStringToObject(j, "profile", r->profile);
    }
    add_hex32(j, "board_crc", r->board_crc);
    return j_print(j);
}

static char *dup_pem(const cJSON *o, const char *k, jerr_t *e)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!cJSON_IsString(v) || strncmp(v->valuestring, "-----BEGIN CERTIFICATE-----", 27) != 0 ||
        strlen(v->valuestring) > 8192) {
        j_fail(e, "'%s' must be a PEM certificate", k);
        return NULL;
    }
    size_t n = strlen(v->valuestring) + 1;
    char *s = malloc(n);
    if (s) {
        memcpy(s, v->valuestring, n);
    } else {
        j_fail(e, "out of memory");
    }
    return s;
}

int ul_enroll_resp_decode(const char *json, size_t len, ul_enroll_resp_t *r, char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    memset(r, 0, sizeof(*r));
    cJSON *root = j_parse_object(json, len, &e);
    if (!root) {
        return J_BAD;
    }
    int rc = J_BAD;
    long long v;
    if (cJSON_GetObjectItemCaseSensitive(root, "error")) {
        if (j_str(root, "error", r->error, sizeof(r->error), true, ul_is_key, &e) < 0) {
            goto out;
        }
        r->ok = false;
        rc = 0;
        goto out;
    }
    if (j_str(root, "device_id", r->device_id, sizeof(r->device_id), true, ul_is_device_id, &e) < 0) {
        goto out;
    }
    const cJSON *m = cJSON_GetObjectItemCaseSensitive(root, "mqtt");
    if (!cJSON_IsObject(m) || j_str(m, "host", r->mqtt_host, sizeof(r->mqtt_host), true, is_name, &e) < 0 ||
        j_int(m, "port", 1, 65535, &v, true, &e) < 0) {
        if (!cJSON_IsObject(m)) {
            j_fail(&e, "missing 'mqtt'");
        }
        goto out;
    }
    r->mqtt_port = (uint16_t)v;
    if (!(r->cert = dup_pem(root, "cert", &e)) || !(r->ca_chain = dup_pem(root, "ca_chain", &e))) {
        ul_enroll_resp_free(r);
        goto out;
    }
    r->ok = true;
    rc = 0;
out:
    cJSON_Delete(root);
    return rc;
}

void ul_enroll_resp_free(ul_enroll_resp_t *r)
{
    free(r->cert);
    free(r->ca_chain);
    r->cert = r->ca_chain = NULL;
}

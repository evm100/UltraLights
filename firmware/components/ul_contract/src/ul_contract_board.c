/* Names, board descriptor and ul_board partition payload (strict decoding). */
#include <stdlib.h>
#include <string.h>
#include "ul_json.h"

static const char *const CHIP_NAMES[] = {"esp32c3", "esp32c6"};
static const char *const KIND_NAMES[] = {"white", "cct", "rgb", "rgbw", "rgbcct",
                                         "argb", "relay", "solenoid", "motion"};
static const char *const ROLE_NAMES[] = {"w", "warm", "cool", "r", "g", "b", "data", "coil", "in"};
static const char *const CH_TYPE_NAMES[] = {"pwm", "argb", "switch", "motion"};
static const char *const ORDER_NAMES[] = {"GRB", "RGB", "BRG", "RBG", "GBR", "BGR", "GRBW", "RGBW"};
static const char *const CHIPSET_NAMES[] = {"ws2812", "sk6812", "ws2811"};
static const char *const OP_NAMES[] = {"identify", "reboot", "wifi_reset", "factory_reset",
                                       "pulse", "ota", "selftest"};

#define N(a) (int)(sizeof(a) / sizeof((a)[0]))

static int find_name(const char *const *names, int n, const char *s)
{
    for (int i = 0; i < n; i++) {
        if (strcmp(names[i], s) == 0) {
            return i;
        }
    }
    return -1;
}

const char *ul_chip_name(ul_chip_t c) { return (unsigned)c < UL_CHIP_COUNT ? CHIP_NAMES[c] : "?"; }
const char *ul_kind_name(ul_kind_t k) { return (unsigned)k < UL_KIND_COUNT ? KIND_NAMES[k] : "?"; }
const char *ul_role_name(ul_role_t r) { return (unsigned)r < UL_ROLE_COUNT ? ROLE_NAMES[r] : "?"; }
const char *ul_ch_type_name(ul_ch_type_t t) { return (unsigned)t < (unsigned)N(CH_TYPE_NAMES) ? CH_TYPE_NAMES[t] : "?"; }
const char *ul_order_name(ul_pixel_order_t o) { return (unsigned)o < (unsigned)N(ORDER_NAMES) ? ORDER_NAMES[o] : "?"; }
const char *ul_op_name(ul_op_t op) { return (unsigned)op < (unsigned)N(OP_NAMES) ? OP_NAMES[op] : "?"; }

int ul_chip_from_name(const char *s, ul_chip_t *out)
{
    int i = find_name(CHIP_NAMES, N(CHIP_NAMES), s);
    if (i < 0) {
        return -1;
    }
    *out = (ul_chip_t)i;
    return 0;
}

/* Internal lookups used by the message codec. */
int ul__kind_from_name(const char *s) { return find_name(KIND_NAMES, N(KIND_NAMES), s); }
int ul__role_from_name(const char *s) { return find_name(ROLE_NAMES, N(ROLE_NAMES), s); }
int ul__op_from_name(const char *s) { return find_name(OP_NAMES, N(OP_NAMES), s); }

uint32_t ul_kind_caps(ul_kind_t k)
{
    switch (k) {
    case UL_KIND_WHITE: return UL_CAP_ONOFF | UL_CAP_LEVEL;
    case UL_KIND_CCT: return UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_CCT;
    case UL_KIND_RGB: return UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_COLOR;
    case UL_KIND_RGBW: return UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_COLOR | UL_CAP_WHITE;
    case UL_KIND_RGBCCT: return UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_COLOR | UL_CAP_CCT;
    case UL_KIND_ARGB: return UL_CAP_ONOFF | UL_CAP_LEVEL | UL_CAP_COLOR | UL_CAP_EFFECT;
    case UL_KIND_RELAY: return UL_CAP_ONOFF;
    case UL_KIND_SOLENOID: return UL_CAP_PULSE;
    case UL_KIND_MOTION: return UL_CAP_MOTION;
    default: return 0;
    }
}

static bool is_name(const char *s) { return s[0] != '\0'; }

/* ---------- board ---------- */

static int channel_from_cjson(const cJSON *c, ul_channel_desc_t *ch, jerr_t *e)
{
    if (!cJSON_IsObject(c)) {
        return j_fail(e, "channel must be an object");
    }
    memset(ch, 0, sizeof(*ch));
    ch->hz = 19000;
    ch->order = UL_ORDER_GRB;
    ch->chipset = UL_CHIPSET_WS2812;
    char type[8];
    long long v;
    if (j_str(c, "id", ch->id, sizeof(ch->id), true, ul_is_key, e) < 0 ||
        j_str(c, "name", ch->name, sizeof(ch->name), false, is_name, e) < 0 ||
        j_str(c, "type", type, sizeof(type), true, NULL, e) < 0 ||
        j_int(c, "gpio", 0, 30, &v, true, e) < 0) {
        return J_BAD;
    }
    ch->gpio = (int)v;
    int t = find_name(CH_TYPE_NAMES, N(CH_TYPE_NAMES), type);
    if (t < 0) {
        return j_fail(e, "channel %s: unknown type '%s'", ch->id, type);
    }
    ch->type = (ul_ch_type_t)t;
    switch (ch->type) {
    case UL_CH_PWM: {
        static const char *const ok[] = {"id", "name", "type", "gpio", "hz", "invert", NULL};
        if (j_only(c, ok, ch->id, e) < 0 || j_int(c, "hz", 100, 40000, &v, false, e) < 0 ||
            j_bool(c, "invert", &ch->invert, false, e) < 0) {
            return J_BAD;
        }
        if (cJSON_GetObjectItemCaseSensitive(c, "hz")) {
            ch->hz = (uint32_t)v;
        }
        break;
    }
    case UL_CH_ARGB: {
        static const char *const ok[] = {"id", "name", "type", "gpio", "pixels", "order", "chipset", NULL};
        char s[8];
        if (j_only(c, ok, ch->id, e) < 0 || j_int(c, "pixels", 1, UL_MAX_PIXELS, &v, true, e) < 0) {
            return J_BAD;
        }
        ch->pixels = (uint16_t)v;
        int r = j_str(c, "order", s, sizeof(s), false, NULL, e);
        if (r < 0) {
            return J_BAD;
        }
        if (r == J_OK) {
            int o = find_name(ORDER_NAMES, N(ORDER_NAMES), s);
            if (o < 0) {
                return j_fail(e, "channel %s: unknown order '%s'", ch->id, s);
            }
            ch->order = (ul_pixel_order_t)o;
        }
        r = j_str(c, "chipset", s, sizeof(s), false, NULL, e);
        if (r < 0) {
            return J_BAD;
        }
        if (r == J_OK) {
            int o = find_name(CHIPSET_NAMES, N(CHIPSET_NAMES), s);
            if (o < 0) {
                return j_fail(e, "channel %s: unknown chipset '%s'", ch->id, s);
            }
            ch->chipset = (ul_chipset_t)o;
        }
        break;
    }
    case UL_CH_SWITCH:
    case UL_CH_MOTION: {
        static const char *const ok[] = {"id", "name", "type", "gpio", "active_low", NULL};
        if (j_only(c, ok, ch->id, e) < 0 || j_bool(c, "active_low", &ch->active_low, false, e) < 0) {
            return J_BAD;
        }
        break;
    }
    }
    return J_OK;
}

static int pin_obj(const cJSON *o, const char *k, int *gpio, bool *active_low, uint32_t *hold_ms, jerr_t *e)
{
    const cJSON *b = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!cJSON_IsObject(b)) {
        return j_fail(e, "'%s' must be an object", k);
    }
    static const char *const ok_reset[] = {"gpio", "active_low", "hold_ms", NULL};
    static const char *const ok_led[] = {"gpio", "active_low", NULL};
    long long v;
    if (j_only(b, hold_ms ? ok_reset : ok_led, k, e) < 0 || j_int(b, "gpio", 0, 30, &v, true, e) < 0) {
        return J_BAD;
    }
    *gpio = (int)v;
    if (j_bool(b, "active_low", active_low, false, e) < 0) {
        return J_BAD;
    }
    if (hold_ms) {
        int r = j_int(b, "hold_ms", 1000, 30000, &v, false, e);
        if (r < 0) {
            return J_BAD;
        }
        if (r == J_OK) {
            *hold_ms = (uint32_t)v;
        }
    }
    return J_OK;
}

int ul_board_from_cjson(const cJSON *j, ul_board_desc_t *b, char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    static const char *const ok[] = {"v", "chip", "board", "hw_rev", "channels", "reset_button", "status_led", NULL};
    long long v;
    char chip[12];
    memset(b, 0, sizeof(*b));
    b->reset_active_low = true;
    b->reset_hold_ms = 5000;
    if (!cJSON_IsObject(j)) {
        return j_fail(&e, "board must be an object");
    }
    if (j_only(j, ok, "board", &e) < 0 || j_int(j, "v", 1, 1, &v, true, &e) < 0 ||
        j_str(j, "chip", chip, sizeof(chip), true, NULL, &e) < 0 ||
        j_str(j, "board", b->board, sizeof(b->board), true, is_name, &e) < 0 ||
        j_str(j, "hw_rev", b->hw_rev, sizeof(b->hw_rev), false, NULL, &e) < 0) {
        return J_BAD;
    }
    if (ul_chip_from_name(chip, &b->chip) != 0) {
        return j_fail(&e, "unknown chip '%s'", chip);
    }
    const cJSON *chs = cJSON_GetObjectItemCaseSensitive(j, "channels");
    if (!cJSON_IsArray(chs)) {
        return j_fail(&e, "missing 'channels' array");
    }
    if (cJSON_GetArraySize(chs) > UL_MAX_CHANNELS) {
        return j_fail(&e, "too many channels");
    }
    const cJSON *c;
    cJSON_ArrayForEach(c, chs) {
        if (channel_from_cjson(c, &b->ch[b->n_channels], &e) < 0) {
            return J_BAD;
        }
        b->n_channels++;
    }
    if (cJSON_GetObjectItemCaseSensitive(j, "reset_button")) {
        b->has_reset = true;
        if (pin_obj(j, "reset_button", &b->reset_gpio, &b->reset_active_low, &b->reset_hold_ms, &e) < 0) {
            return J_BAD;
        }
    }
    if (cJSON_GetObjectItemCaseSensitive(j, "status_led")) {
        b->has_status = true;
        if (pin_obj(j, "status_led", &b->status_gpio, &b->status_active_low, NULL, &e) < 0) {
            return J_BAD;
        }
    }
    return 0;
}

cJSON *ul_channel_to_cjson(const ul_channel_desc_t *ch)
{
    cJSON *c = cJSON_CreateObject();
    cJSON_AddStringToObject(c, "id", ch->id);
    if (ch->name[0]) {
        cJSON_AddStringToObject(c, "name", ch->name);
    }
    cJSON_AddStringToObject(c, "type", ul_ch_type_name(ch->type));
    cJSON_AddNumberToObject(c, "gpio", ch->gpio);
    switch (ch->type) {
    case UL_CH_PWM:
        cJSON_AddNumberToObject(c, "hz", ch->hz);
        if (ch->invert) {
            cJSON_AddBoolToObject(c, "invert", true);
        }
        break;
    case UL_CH_ARGB:
        cJSON_AddNumberToObject(c, "pixels", ch->pixels);
        cJSON_AddStringToObject(c, "order", ul_order_name(ch->order));
        if (ch->chipset != UL_CHIPSET_WS2812) {
            cJSON_AddStringToObject(c, "chipset", CHIPSET_NAMES[ch->chipset]);
        }
        break;
    case UL_CH_SWITCH:
    case UL_CH_MOTION:
        if (ch->active_low) {
            cJSON_AddBoolToObject(c, "active_low", true);
        }
        break;
    }
    return c;
}

cJSON *ul_board_to_cjson(const ul_board_desc_t *b)
{
    cJSON *j = cJSON_CreateObject();
    cJSON_AddNumberToObject(j, "v", 1);
    cJSON_AddStringToObject(j, "chip", ul_chip_name(b->chip));
    cJSON_AddStringToObject(j, "board", b->board);
    if (b->hw_rev[0]) {
        cJSON_AddStringToObject(j, "hw_rev", b->hw_rev);
    }
    cJSON *chs = cJSON_AddArrayToObject(j, "channels");
    for (int i = 0; i < b->n_channels; i++) {
        cJSON_AddItemToArray(chs, ul_channel_to_cjson(&b->ch[i]));
    }
    if (b->has_reset) {
        cJSON *r = cJSON_AddObjectToObject(j, "reset_button");
        cJSON_AddNumberToObject(r, "gpio", b->reset_gpio);
        cJSON_AddBoolToObject(r, "active_low", b->reset_active_low);
        cJSON_AddNumberToObject(r, "hold_ms", b->reset_hold_ms);
    }
    if (b->has_status) {
        cJSON *r = cJSON_AddObjectToObject(j, "status_led");
        cJSON_AddNumberToObject(r, "gpio", b->status_gpio);
        cJSON_AddBoolToObject(r, "active_low", b->status_active_low);
    }
    return j;
}

/* ---------- partition payload ---------- */

static bool is_token(const char *s)
{
    size_t n = strlen(s);
    if (n < 32 || n > 64) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '-')) {
            return false;
        }
    }
    return true;
}

static bool is_host(const char *s)
{
    size_t n = strlen(s);
    if (n < 1 || n > 253) {
        return false;
    }
    for (size_t i = 0; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '-')) {
            return false;
        }
    }
    return true;
}

static bool is_pem_cert(const char *s) { return strncmp(s, "-----BEGIN CERTIFICATE-----", 27) == 0; }
static bool is_ssid(const char *s) { return s[0] != '\0'; }

int ul_partition_decode(const char *json, size_t len, ul_board_desc_t *board,
                        ul_provision_t *p, char *err, size_t errlen)
{
    jerr_t e = {err, errlen};
    cJSON *root = j_parse_object(json, len, &e);
    if (!root) {
        return J_BAD;
    }
    int rc = J_BAD;
    long long v;
    static const char *const ok[] = {"v", "board", "provision", NULL};
    static const char *const ok_p[] = {"device_id", "token", "server", "mqtt_port", "server_ca", "wifi", "dns", NULL};
    static const char *const ok_w[] = {"ssid", "psk", NULL};
    memset(p, 0, sizeof(*p));
    p->mqtt_port = 8883;
    if (j_only(root, ok, "partition", &e) < 0 || j_int(root, "v", 1, 1, &v, true, &e) < 0) {
        goto out;
    }
    if (ul_board_from_cjson(cJSON_GetObjectItemCaseSensitive(root, "board"), board, err, errlen) < 0) {
        goto out;
    }
    const cJSON *pj = cJSON_GetObjectItemCaseSensitive(root, "provision");
    if (!cJSON_IsObject(pj)) {
        j_fail(&e, "missing 'provision'");
        goto out;
    }
    if (j_only(pj, ok_p, "provision", &e) < 0 ||
        j_str(pj, "device_id", p->device_id, sizeof(p->device_id), true, ul_is_device_id, &e) < 0 ||
        j_str(pj, "token", p->token, sizeof(p->token), false, is_token, &e) < 0 ||
        j_str(pj, "server", p->server, sizeof(p->server), true, is_host, &e) < 0 ||
        j_str(pj, "server_ca", p->server_ca, sizeof(p->server_ca), false, is_pem_cert, &e) < 0) {
        goto out;
    }
    int r = j_int(pj, "mqtt_port", 1, 65535, &v, false, &e);
    if (r < 0) {
        goto out;
    }
    if (r == J_OK) {
        p->mqtt_port = (uint16_t)v;
    }
    r = j_str(pj, "dns", p->dns, sizeof(p->dns), false, ul_is_ipv4, &e);
    if (r < 0) {
        goto out;
    }
    p->has_dns = (r == J_OK);
    const cJSON *w = cJSON_GetObjectItemCaseSensitive(pj, "wifi");
    if (w) {
        if (!cJSON_IsObject(w) || j_only(w, ok_w, "wifi", &e) < 0 ||
            j_str(w, "ssid", p->ssid, sizeof(p->ssid), true, is_ssid, &e) < 0 ||
            j_str(w, "psk", p->psk, sizeof(p->psk), false, NULL, &e) < 0) {
            if (!cJSON_IsObject(w)) {
                j_fail(&e, "'wifi' must be an object");
            }
            goto out;
        }
        p->has_wifi = true;
    }
    rc = 0;
out:
    cJSON_Delete(root);
    return rc;
}

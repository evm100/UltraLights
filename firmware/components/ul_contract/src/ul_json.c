#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "ul_json.h"

int j_fail(jerr_t *e, const char *fmt, ...)
{
    if (e && e->err && e->errlen) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(e->err, e->errlen, fmt, ap);
        va_end(ap);
    }
    return J_BAD;
}

int j_only(const cJSON *o, const char *const *allowed, const char *what, jerr_t *e)
{
    for (const cJSON *c = o->child; c; c = c->next) {
        bool known = false;
        for (const char *const *a = allowed; *a; a++) {
            if (strcmp(c->string, *a) == 0) {
                known = true;
                break;
            }
        }
        if (!known) {
            return j_fail(e, "%s: unknown field '%s'", what, c->string);
        }
    }
    return J_OK;
}

int j_str(const cJSON *o, const char *k, char *dst, size_t dstsz, bool required,
          bool (*valid)(const char *), jerr_t *e)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!v) {
        return required ? j_fail(e, "missing '%s'", k) : J_ABSENT;
    }
    if (!cJSON_IsString(v)) {
        return j_fail(e, "'%s' must be a string", k);
    }
    size_t n = strlen(v->valuestring);
    if (n >= dstsz) {
        return j_fail(e, "'%s' too long", k);
    }
    if (valid && !valid(v->valuestring)) {
        return j_fail(e, "'%s' has an invalid value", k);
    }
    memcpy(dst, v->valuestring, n + 1);
    return J_OK;
}

int j_int(const cJSON *o, const char *k, long long min, long long max, long long *dst,
          bool required, jerr_t *e)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!v) {
        return required ? j_fail(e, "missing '%s'", k) : J_ABSENT;
    }
    if (!cJSON_IsNumber(v) || v->valuedouble != floor(v->valuedouble)) {
        return j_fail(e, "'%s' must be an integer", k);
    }
    if (v->valuedouble < (double)min || v->valuedouble > (double)max) {
        return j_fail(e, "'%s' out of range", k);
    }
    *dst = (long long)v->valuedouble;
    return J_OK;
}

int j_num(const cJSON *o, const char *k, double min, double max, double *dst,
          bool required, jerr_t *e)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!v) {
        return required ? j_fail(e, "missing '%s'", k) : J_ABSENT;
    }
    if (!cJSON_IsNumber(v) || isnan(v->valuedouble)) {
        return j_fail(e, "'%s' must be a number", k);
    }
    if (v->valuedouble < min || v->valuedouble > max) {
        return j_fail(e, "'%s' out of range", k);
    }
    *dst = v->valuedouble;
    return J_OK;
}

int j_bool(const cJSON *o, const char *k, bool *dst, bool required, jerr_t *e)
{
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    if (!v) {
        return required ? j_fail(e, "missing '%s'", k) : J_ABSENT;
    }
    if (!cJSON_IsBool(v)) {
        return j_fail(e, "'%s' must be true or false", k);
    }
    *dst = cJSON_IsTrue(v);
    return J_OK;
}

cJSON *j_parse_object(const char *json, size_t len, jerr_t *e)
{
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) {
        j_fail(e, "not valid JSON");
        return NULL;
    }
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        j_fail(e, "not a JSON object");
        return NULL;
    }
    return root;
}

char *j_print(cJSON *root)
{
    if (!root) {
        return NULL;
    }
    char *s = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return s;
}

/* ---------- value checks ---------- */

bool ul_is_key(const char *s)
{
    size_t n = strlen(s);
    if (n < 1 || n > 16 || !(s[0] >= 'a' && s[0] <= 'z')) {
        return false;
    }
    for (size_t i = 1; i < n; i++) {
        char c = s[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_')) {
            return false;
        }
    }
    return true;
}

bool ul_is_device_id(const char *s)
{
    if (strlen(s) != 9 || strncmp(s, "ul-", 3) != 0) {
        return false;
    }
    for (int i = 3; i < 9; i++) {
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) {
            return false;
        }
    }
    return true;
}

bool ul_is_ipv4(const char *s)
{
    int parts = 0;
    const char *p = s;
    while (*p) {
        if (!isdigit((unsigned char)*p)) {
            return false;
        }
        int v = 0, digits = 0;
        while (isdigit((unsigned char)*p)) {
            v = v * 10 + (*p - '0');
            p++;
            if (++digits > 3) {
                return false;
            }
        }
        if (v > 255 || (digits > 1 && p[-digits] == '0')) {
            return false;
        }
        parts++;
        if (*p == '.') {
            p++;
            if (!*p) {
                return false;
            }
        } else if (*p) {
            return false;
        }
    }
    return parts == 4;
}

bool ul_is_src(const char *s)
{
    /* ^[a-z]+(:[A-Za-z0-9_.-]+)*$, max 64 */
    size_t n = strlen(s);
    if (n == 0 || n > 64) {
        return false;
    }
    const char *p = s;
    if (!(*p >= 'a' && *p <= 'z')) {
        return false;
    }
    while (*p >= 'a' && *p <= 'z') {
        p++;
    }
    while (*p == ':') {
        p++;
        const char *start = p;
        while (*p && *p != ':') {
            char c = *p;
            if (!(isalnum((unsigned char)c) || c == '_' || c == '.' || c == '-')) {
                return false;
            }
            p++;
        }
        if (p == start) {
            return false;
        }
    }
    return *p == '\0';
}

bool ul_is_semver(const char *s)
{
    const char *p = s;
    for (int part = 0; part < 3; part++) {
        if (!isdigit((unsigned char)*p)) {
            return false;
        }
        while (isdigit((unsigned char)*p)) {
            p++;
        }
        if (part < 2) {
            if (*p != '.') {
                return false;
            }
            p++;
        }
    }
    if (*p == '\0') {
        return true;
    }
    if (*p != '-' || !p[1]) {
        return false;
    }
    for (p++; *p; p++) {
        if (!(isalnum((unsigned char)*p) || *p == '.' || *p == '-')) {
            return false;
        }
    }
    return true;
}

int ul_mac_format(const uint8_t mac[6], char out[18])
{
    return snprintf(out, 18, "%02x:%02x:%02x:%02x:%02x:%02x",
                    mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

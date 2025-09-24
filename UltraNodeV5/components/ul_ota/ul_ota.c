#include "sdkconfig.h"
#include "ul_ota.h"
#include "esp_https_ota.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_tls.h"
#include "ul_core.h"
#include "ul_mqtt.h"
#include <string.h>
#include <stdlib.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "mbedtls/x509_crt.h"

static const char* TAG = "ul_ota";

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    bool failed;
} http_buffer_t;

typedef struct {
    char *binary_url;
    char *binary_url_lan;
    char *version;
    char *sha256_hex;
    char *sig;
    size_t size;
} ul_ota_manifest_t;

static void http_buffer_free(http_buffer_t *buffer)
{
    if (!buffer) {
        return;
    }
    free(buffer->data);
    buffer->data = NULL;
    buffer->len = 0;
    buffer->cap = 0;
    buffer->failed = false;
}

static bool http_buffer_reserve(http_buffer_t *buffer, size_t needed)
{
    if (needed <= buffer->cap) {
        return true;
    }

    size_t new_cap = buffer->cap ? buffer->cap : 256;
    while (new_cap < needed) {
        if (new_cap > SIZE_MAX / 2) {
            buffer->failed = true;
            return false;
        }
        new_cap *= 2;
    }

    char *tmp = realloc(buffer->data, new_cap);
    if (!tmp) {
        buffer->failed = true;
        return false;
    }

    buffer->data = tmp;
    buffer->cap = new_cap;
    return true;
}

static char *dup_string(const char *src)
{
    if (!src) {
        return NULL;
    }
    size_t len = strlen(src) + 1;
    char *dst = malloc(len);
    if (!dst) {
        return NULL;
    }
    memcpy(dst, src, len);
    return dst;
}

static char *strndup_compat(const char *src, size_t len)
{
    if (!src) {
        return NULL;
    }
    char *dst = malloc(len + 1);
    if (!dst) {
        return NULL;
    }
    memcpy(dst, src, len);
    dst[len] = '\0';
    return dst;
}

typedef struct {
    char *scheme;
    char *host;
    char *path;
    int port;
} ul_parsed_url_t;

static void ul_parsed_url_free(ul_parsed_url_t *url)
{
    if (!url) {
        return;
    }
    free(url->scheme);
    free(url->host);
    free(url->path);
    memset(url, 0, sizeof(*url));
}

static bool ul_parse_url(const char *url, ul_parsed_url_t *out)
{
    if (!url || !out) {
        return false;
    }

    const char *scheme_end = strstr(url, "://");
    if (!scheme_end || scheme_end == url) {
        return false;
    }

    memset(out, 0, sizeof(*out));

    out->scheme = strndup_compat(url, (size_t)(scheme_end - url));
    if (!out->scheme) {
        ul_parsed_url_free(out);
        return false;
    }

    const char *authority = scheme_end + 3;
    const char *path_start = strpbrk(authority, "/?#");
    const char *authority_end = path_start ? path_start : authority + strlen(authority);

    const char *host_start = authority;
    const char *host_end = authority_end;
    const char *colon = memchr(host_start, ':', (size_t)(host_end - host_start));
    if (colon) {
        host_end = colon;
        const char *port_start = colon + 1;
        char *endptr = NULL;
        long port = strtol(port_start, &endptr, 10);
        if (endptr && endptr <= authority_end && port > 0 && port <= 65535) {
            out->port = (int)port;
        }
    }

    out->host = strndup_compat(host_start, (size_t)(host_end - host_start));
    if (!out->host) {
        ul_parsed_url_free(out);
        return false;
    }

    if (path_start) {
        const char *path_end = strpbrk(path_start, "?#");
        size_t path_len = path_end ? (size_t)(path_end - path_start) : strlen(path_start);
        out->path = strndup_compat(path_start, path_len);
    } else {
        out->path = dup_string("/");
    }

    if (!out->path) {
        ul_parsed_url_free(out);
        return false;
    }

    return true;
}

static char *ul_resolve_relative_url(const esp_http_client_config_t *cfg, const char *relative)
{
    if (!cfg || !relative) {
        return NULL;
    }

    if (strstr(relative, "://")) {
        return dup_string(relative);
    }

    if (!cfg->url) {
        return NULL;
    }

    ul_parsed_url_t base = {0};
    if (!ul_parse_url(cfg->url, &base)) {
        return NULL;
    }

    if (cfg->host && cfg->host[0]) {
        free(base.host);
        base.host = NULL;
        const char *host_override = cfg->host;
        const char *port_sep = strchr(host_override, ':');
        bool copied_override = false;
        if (port_sep) {
            char *endptr = NULL;
            long override_port = strtol(port_sep + 1, &endptr, 10);
            if (endptr && *endptr == '\0' && override_port > 0 && override_port <= 65535) {
                base.host = strndup_compat(host_override, (size_t)(port_sep - host_override));
                if (!base.host) {
                    ul_parsed_url_free(&base);
                    return NULL;
                }
                if (cfg->port > 0) {
                    base.port = cfg->port;
                } else {
                    base.port = (int)override_port;
                }
                copied_override = true;
            }
        }
        if (!copied_override) {
            base.host = dup_string(host_override);
            if (!base.host) {
                ul_parsed_url_free(&base);
                return NULL;
            }
            if (cfg->port > 0) {
                base.port = cfg->port;
            }
        }
    } else if (cfg->port > 0) {
        base.port = cfg->port;
    }

    char *resolved_path = NULL;
    if (relative[0] == '/') {
        resolved_path = dup_string(relative);
    } else {
        char *base_path = base.path ? dup_string(base.path) : dup_string("/");
        if (!base_path) {
            ul_parsed_url_free(&base);
            return NULL;
        }

        if (!base_path[0]) {
            free(base_path);
            base_path = dup_string("/");
            if (!base_path) {
                ul_parsed_url_free(&base);
                return NULL;
            }
        }

        char *last_slash = strrchr(base_path, '/');
        if (last_slash) {
            last_slash[1] = '\0';
        } else {
            base_path[0] = '/';
            base_path[1] = '\0';
        }

        size_t dir_len = strlen(base_path);
        size_t rel_len = strlen(relative);
        resolved_path = malloc(dir_len + rel_len + 1);
        if (!resolved_path) {
            free(base_path);
            ul_parsed_url_free(&base);
            return NULL;
        }
        memcpy(resolved_path, base_path, dir_len);
        memcpy(resolved_path + dir_len, relative, rel_len + 1);
        free(base_path);
    }

    if (!resolved_path) {
        ul_parsed_url_free(&base);
        return NULL;
    }

    if (resolved_path[0] != '/') {
        size_t rel_len = strlen(resolved_path);
        char *tmp = malloc(rel_len + 2);
        if (!tmp) {
            free(resolved_path);
            ul_parsed_url_free(&base);
            return NULL;
        }
        tmp[0] = '/';
        memcpy(tmp + 1, resolved_path, rel_len + 1);
        free(resolved_path);
        resolved_path = tmp;
    }

    if (cfg->port > 0) {
        base.port = cfg->port;
    }

    char port_buf[16] = {0};
    size_t port_len = 0;
    if (base.port > 0) {
        port_len = (size_t)snprintf(port_buf, sizeof(port_buf), ":%d", base.port);
    }

    size_t scheme_len = strlen(base.scheme);
    size_t host_len = strlen(base.host);
    size_t path_len = strlen(resolved_path);
    size_t total_len = scheme_len + 3 + host_len + port_len + path_len + 1;
    char *full_url = malloc(total_len);
    if (!full_url) {
        free(resolved_path);
        ul_parsed_url_free(&base);
        return NULL;
    }

    snprintf(full_url, total_len, "%s://%s%s%s", base.scheme, base.host, port_len ? port_buf : "", resolved_path);

    free(resolved_path);
    ul_parsed_url_free(&base);
    return full_url;
}

static void ul_ota_manifest_free(ul_ota_manifest_t *manifest)
{
    if (!manifest) {
        return;
    }
    free(manifest->binary_url);
    free(manifest->binary_url_lan);
    free(manifest->version);
    free(manifest->sha256_hex);
    free(manifest->sig);
    memset(manifest, 0, sizeof(*manifest));
}

static esp_err_t manifest_http_event_handler(esp_http_client_event_t *evt)
{
    http_buffer_t *buffer = evt->user_data;

    switch (evt->event_id) {
        case HTTP_EVENT_ON_DATA:
            if (!buffer || !evt->data || evt->data_len <= 0) {
                break;
            }
            if (!http_buffer_reserve(buffer, buffer->len + evt->data_len + 1)) {
                buffer->failed = true;
                ESP_LOGE(TAG, "Failed to grow manifest buffer");
                return ESP_FAIL;
            }
            memcpy(buffer->data + buffer->len, evt->data, evt->data_len);
            buffer->len += evt->data_len;
            buffer->data[buffer->len] = '\0';
            break;
        default:
            break;
    }

    return ESP_OK;
}

static esp_err_t _http_client_init_cb(esp_http_client_handle_t http_client);

static esp_err_t ul_ota_fetch_manifest(const esp_http_client_config_t *base_cfg,
                                       ul_ota_manifest_t *out_manifest)
{
    if (!base_cfg || !out_manifest) {
        return ESP_ERR_INVALID_ARG;
    }

    *out_manifest = (ul_ota_manifest_t){0};
    http_buffer_t buffer = {0};

    esp_http_client_config_t cfg = *base_cfg;
    cfg.event_handler = manifest_http_event_handler;
    cfg.user_data = &buffer;

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        ESP_LOGE(TAG, "Failed to init HTTP client for manifest");
        return ESP_ERR_NO_MEM;
    }

    _http_client_init_cb(client);

    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Manifest download failed: %s", esp_err_to_name(err));
        goto cleanup;
    }

    int status = esp_http_client_get_status_code(client);
    if (status != 200) {
        ESP_LOGE(TAG, "Manifest HTTP status %d", status);
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    if (buffer.failed) {
        ESP_LOGE(TAG, "Manifest buffer allocation failed");
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    if (!buffer.data || buffer.len == 0) {
        ESP_LOGE(TAG, "Empty manifest response");
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    cJSON *root = cJSON_ParseWithLength(buffer.data, buffer.len);
    if (!root) {
        ESP_LOGE(TAG, "Failed to parse manifest JSON");
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    const cJSON *binary_url = cJSON_GetObjectItemCaseSensitive(root, "binary_url");
    if (!cJSON_IsString(binary_url) || !binary_url->valuestring || !binary_url->valuestring[0]) {
        ESP_LOGE(TAG, "Manifest missing binary_url");
        cJSON_Delete(root);
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    out_manifest->binary_url = dup_string(binary_url->valuestring);
    if (!out_manifest->binary_url) {
        cJSON_Delete(root);
        err = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    const cJSON *binary_url_lan = cJSON_GetObjectItemCaseSensitive(root, "binary_url_lan");
    if (cJSON_IsString(binary_url_lan) && binary_url_lan->valuestring && binary_url_lan->valuestring[0]) {
        out_manifest->binary_url_lan = dup_string(binary_url_lan->valuestring);
        if (!out_manifest->binary_url_lan) {
            cJSON_Delete(root);
            err = ESP_ERR_NO_MEM;
            goto cleanup;
        }
    }

    const cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "version");
    if (cJSON_IsString(version) && version->valuestring) {
        out_manifest->version = dup_string(version->valuestring);
    }

    const cJSON *sha = cJSON_GetObjectItemCaseSensitive(root, "sha256_hex");
    if (cJSON_IsString(sha) && sha->valuestring && sha->valuestring[0]) {
        out_manifest->sha256_hex = dup_string(sha->valuestring);
    }

    const cJSON *sig = cJSON_GetObjectItemCaseSensitive(root, "sig");
    if (cJSON_IsString(sig) && sig->valuestring && sig->valuestring[0]) {
        out_manifest->sig = dup_string(sig->valuestring);
    }

    const cJSON *size = cJSON_GetObjectItemCaseSensitive(root, "size");
    if (cJSON_IsNumber(size) && size->valuedouble >= 0) {
        out_manifest->size = (size_t)size->valuedouble;
    }

    cJSON_Delete(root);

cleanup:
    esp_http_client_cleanup(client);
    http_buffer_free(&buffer);

    if (err != ESP_OK) {
        ul_ota_manifest_free(out_manifest);
    }

    return err;
}

static void log_ota_error_hint(esp_err_t err, esp_https_ota_handle_t handle)
{
    (void)handle; // unused on esp-idf versions without error-handle API

    int esp_tls_err = 0;
    int cert_verify_flags = 0;

    esp_tls_get_and_clear_last_error(NULL, &esp_tls_err, &cert_verify_flags);

    if (esp_tls_err || cert_verify_flags) {
        ESP_LOGW(TAG, "TLS err=%d, flags=0x%x", esp_tls_err, cert_verify_flags);
        if (esp_tls_err == ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME) {
            ESP_LOGW(TAG, "DNS lookup failed. Check DNS server or set UL_OTA_SERVER_HOST");
        }
        if (cert_verify_flags & MBEDTLS_X509_BADCERT_EXPIRED) {
            ESP_LOGW(TAG, "Server certificate expired");
        }
        if (cert_verify_flags & MBEDTLS_X509_BADCERT_NOT_TRUSTED) {
            ESP_LOGW(TAG, "Certificate not trusted; verify CA bundle");
        }
        if (cert_verify_flags & MBEDTLS_X509_BADCERT_CN_MISMATCH) {
            ESP_LOGW(TAG, "Certificate common name mismatch");
        }
    }

    switch (err) {
        case ESP_ERR_HTTP_CONNECT:
            ESP_LOGW(TAG, "Connection failed. Verify server URL and network reachability");
            ESP_LOGW(TAG, "If using a local OTA server, ensure your router supports NAT hairpinning or set UL_OTA_SERVER_HOST to the LAN IP");
            break;
        case ESP_ERR_NO_MEM:
            ESP_LOGW(TAG, "Not enough memory for OTA operation");
            break;
        default:
            ESP_LOGW(TAG, "See esp_err_to_name for more details");
            break;
    }
}

static esp_err_t _http_event_handler(esp_http_client_event_t *evt)
{
    switch (evt->event_id) {
        case HTTP_EVENT_ERROR:
            ESP_LOGD(TAG, "HTTP_EVENT_ERROR");
            break;
        case HTTP_EVENT_ON_CONNECTED:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_CONNECTED");
            break;
        case HTTP_EVENT_HEADER_SENT:
            ESP_LOGD(TAG, "HTTP_EVENT_HEADER_SENT");
            break;
        case HTTP_EVENT_ON_HEADER:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_HEADER: %s: %s", evt->header_key, evt->header_value);
            break;
        case HTTP_EVENT_ON_DATA:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_DATA: %d bytes", evt->data_len);
            break;
        case HTTP_EVENT_ON_FINISH:
            ESP_LOGD(TAG, "HTTP_EVENT_ON_FINISH");
            break;
        case HTTP_EVENT_DISCONNECTED:
            ESP_LOGD(TAG, "HTTP_EVENT_DISCONNECTED");
            break;
        default:
            break;
    }
    return ESP_OK;
}

static esp_err_t _http_client_init_cb(esp_http_client_handle_t http_client)
{
    // Inject Bearer token header
    if (strlen(CONFIG_UL_OTA_BEARER_TOKEN)) {
        char bearer[160];
        snprintf(bearer, sizeof(bearer), "Bearer %s", CONFIG_UL_OTA_BEARER_TOKEN);
        esp_http_client_set_header(http_client, "Authorization", bearer);
    }
    return ESP_OK;
}

void ul_ota_check_now(bool force)
{
    if (!ul_core_is_connected()) {
        ESP_LOGW(TAG, "Network not connected, skipping OTA check");
        ul_mqtt_publish_ota_event("skipped", "network_down");
        return;
    }
    ESP_LOGI(TAG, "OTA check (force=%d): %s", force, CONFIG_UL_OTA_MANIFEST_URL);
    ul_mqtt_publish_ota_event("check_start", CONFIG_UL_OTA_MANIFEST_URL);

    esp_http_client_config_t http_cfg = {
        .url = CONFIG_UL_OTA_MANIFEST_URL,
        .timeout_ms = 10000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .event_handler = _http_event_handler,
    };

    if (strlen(CONFIG_UL_OTA_SERVER_HOST)) {
        http_cfg.host = CONFIG_UL_OTA_SERVER_HOST;
        ESP_LOGI(TAG, "Using OTA host override: %s", CONFIG_UL_OTA_SERVER_HOST);
    }
    if (strlen(CONFIG_UL_OTA_COMMON_NAME)) {
        http_cfg.common_name = CONFIG_UL_OTA_COMMON_NAME;
    }

    ul_ota_manifest_t manifest = {0};
    bool have_manifest = false;
    const char *ota_url = NULL;
    char *resolved_ota_url = NULL;

    esp_err_t err = ul_ota_fetch_manifest(&http_cfg, &manifest);
    if (err != ESP_OK) {
        ul_mqtt_publish_ota_event("manifest_fail", esp_err_to_name(err));
        ESP_LOGE(TAG, "Failed to fetch OTA manifest: %s", esp_err_to_name(err));
        log_ota_error_hint(err, NULL);
        goto cleanup;
    }
    have_manifest = true;

    if (manifest.binary_url_lan && strlen(CONFIG_UL_OTA_SERVER_HOST)) {
        ota_url = manifest.binary_url_lan;
        ESP_LOGI(TAG, "Using LAN OTA URL from manifest");
    } else {
        ota_url = manifest.binary_url;
    }

    if (!ota_url) {
        ul_mqtt_publish_ota_event("manifest_fail", "missing_binary_url");
        ESP_LOGE(TAG, "Manifest did not provide a binary_url");
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    resolved_ota_url = ul_resolve_relative_url(&http_cfg, ota_url);
    if (!resolved_ota_url) {
        ul_mqtt_publish_ota_event("manifest_fail", "invalid_binary_url");
        ESP_LOGE(TAG, "Failed to resolve OTA URL from manifest entry: %s", ota_url);
        err = ESP_ERR_INVALID_RESPONSE;
        goto cleanup;
    }

    const char *manifest_version = manifest.version ? manifest.version : "unknown";
    const char *manifest_sha = manifest.sha256_hex ? manifest.sha256_hex : "n/a";
    if (manifest.size > 0) {
        ESP_LOGI(TAG, "Manifest version=%s size=%zu sha256=%s",
                 manifest_version,
                 manifest.size,
                 manifest_sha);
    } else {
        ESP_LOGI(TAG, "Manifest version=%s size=unknown sha256=%s",
                 manifest_version,
                 manifest_sha);
    }
    ESP_LOGI(TAG, "OTA image URL: %s", resolved_ota_url);
    ul_mqtt_publish_ota_event("manifest_ok", resolved_ota_url);

    esp_http_client_config_t ota_http_cfg = http_cfg;
    ota_http_cfg.url = resolved_ota_url;

    ota_http_cfg.event_handler = _http_event_handler;
    ota_http_cfg.user_data = NULL;

    esp_https_ota_config_t ota_cfg = {
        .http_config = &ota_http_cfg,
        .http_client_init_cb = _http_client_init_cb,
    };
    esp_https_ota_handle_t handle = NULL;
    ESP_LOGD(TAG, "Starting HTTPS OTA");
    ul_mqtt_publish_ota_event("begin", NULL);
    err = esp_https_ota_begin(&ota_cfg, &handle);
    if (err == ESP_OK) {
        while ((err = esp_https_ota_perform(handle)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
            ;
        }
        if (err == ESP_OK && esp_https_ota_is_complete_data_received(handle)) {
            if (esp_https_ota_finish(handle) == ESP_OK) {
                ul_mqtt_publish_ota_event("success", manifest.version ? manifest.version : NULL);
                if (manifest.version) {
                    ESP_LOGI(TAG, "OTA successful (version %s)", manifest.version);
                } else {
                    ESP_LOGI(TAG, "OTA successful");
                }
                if (have_manifest) {
                    ul_ota_manifest_free(&manifest);
                    have_manifest = false;
                }
                free(resolved_ota_url);
                resolved_ota_url = NULL;

                ESP_LOGI(TAG, "Rebooting after OTA");
                esp_restart();
            } else {
                ul_mqtt_publish_ota_event("finish_fail", esp_err_to_name(err));
                ESP_LOGE(TAG, "OTA finish failed");
                log_ota_error_hint(err, handle);
                esp_https_ota_abort(handle);
            }
        } else {
            ul_mqtt_publish_ota_event("perform_fail", esp_err_to_name(err));
            ESP_LOGE(TAG, "OTA perform failed: %s", esp_err_to_name(err));
            log_ota_error_hint(err, handle);
            esp_https_ota_abort(handle);
        }
    } else {
        ul_mqtt_publish_ota_event("begin_fail", esp_err_to_name(err));
        ESP_LOGE(TAG, "OTA begin failed: %s", esp_err_to_name(err));
        log_ota_error_hint(err, handle);
    }

cleanup:
    free(resolved_ota_url);

    if (have_manifest) {
        ul_ota_manifest_free(&manifest);
    }
}


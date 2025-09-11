#include "ul_ota.h"
#include "sdkconfig.h"
#include "esp_https_ota.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_tls.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ul_task.h"
#include "ul_core.h"
#include <string.h>
#include "esp_crt_bundle.h"
#include "mbedtls/x509_crt.h"

static const char* TAG = "ul_ota";
static TaskHandle_t s_ota_task = NULL;

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

static void ota_task(void*)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(CONFIG_UL_OTA_INTERVAL_S * 1000));
        ul_ota_check_now(false);
    }
}

void ul_ota_start(void)
{
    // Periodic OTA checks pinned to core 0 when multiple cores are available
    ul_task_create(ota_task, "ota_task", 6144, NULL, 4, &s_ota_task, 0);
}

void ul_ota_stop(void)
{
    if (s_ota_task) {
        vTaskDelete(s_ota_task);
        s_ota_task = NULL;
    }
}

static esp_err_t _http_client_init_cb(esp_http_client_handle_t http_client)
{
    // Inject Bearer token header
    char bearer[160];
    snprintf(bearer, sizeof(bearer), "Bearer %s", CONFIG_UL_OTA_BEARER_TOKEN);
    esp_http_client_set_header(http_client, "Authorization", bearer);
    return ESP_OK;
}

void ul_ota_check_now(bool force)
{
    if (!ul_core_is_connected()) {
        ESP_LOGW(TAG, "Network not connected, skipping OTA check");
        return;
    }
    ESP_LOGI(TAG, "OTA check (force=%d): %s", force, CONFIG_UL_OTA_MANIFEST_URL);

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

    // In a full implementation, fetch manifest, verify HMAC, then esp_https_ota on the URL within.
    // Here we directly try OTA from manifest URL for skeleton purposes.
    esp_https_ota_config_t ota_cfg = {
        .http_config = &http_cfg,
        .http_client_init_cb = _http_client_init_cb,
    };
    esp_https_ota_handle_t handle = NULL;
    ESP_LOGD(TAG, "Starting HTTPS OTA");
    esp_err_t err = esp_https_ota_begin(&ota_cfg, &handle);
    if (err == ESP_OK) {
        while ((err = esp_https_ota_perform(handle)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
            ;
        }
        if (err == ESP_OK && esp_https_ota_is_complete_data_received(handle)) {
            if (esp_https_ota_finish(handle) == ESP_OK) {
                ESP_LOGI(TAG, "OTA successful, rebooting...");
                esp_restart();
            } else {
                ESP_LOGE(TAG, "OTA finish failed");
                log_ota_error_hint(err, handle);
            }
        } else {
            ESP_LOGE(TAG, "OTA perform failed: %s", esp_err_to_name(err));
            log_ota_error_hint(err, handle);
            esp_https_ota_abort(handle);
        }
    } else {
        ESP_LOGE(TAG, "OTA begin failed: %s", esp_err_to_name(err));
        log_ota_error_hint(err, handle);
    }
}

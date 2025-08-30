#include "ul_ota.h"
#include "sdkconfig.h"
#include "esp_https_ota.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ul_task.h"
#include <string.h>
#include "esp_crt_bundle.h"

static const char* TAG = "ul_ota";

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
    ul_task_create(ota_task, "ota_task", 6144, NULL, 4, NULL, 0);
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
    ESP_LOGI(TAG, "OTA check (force=%d): %s", force, CONFIG_UL_OTA_MANIFEST_URL);

    esp_http_client_config_t http_cfg = {
        .url = CONFIG_UL_OTA_MANIFEST_URL,
        .timeout_ms = 10000,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .event_handler = NULL,
    };

    // In a full implementation, fetch manifest, verify HMAC, then esp_https_ota on the URL within.
    // Here we directly try OTA from manifest URL for skeleton purposes.
    esp_https_ota_config_t ota_cfg = {
        .http_config = &http_cfg,
        .http_client_init_cb = _http_client_init_cb,
    };
    esp_https_ota_handle_t handle = NULL;
    if (esp_https_ota_begin(&ota_cfg, &handle) == ESP_OK) {
        esp_err_t err = esp_https_ota_perform(handle);
        if (err == ESP_OK && esp_https_ota_is_complete_data_received(handle)) {
            if (esp_https_ota_finish(handle) == ESP_OK) {
                ESP_LOGI(TAG, "OTA successful, rebooting...");
                esp_restart();
            } else {
                ESP_LOGE(TAG, "OTA finish failed");
            }
        } else {
            ESP_LOGE(TAG, "OTA perform failed: %s", esp_err_to_name(err));
            esp_https_ota_abort(handle);
        }
    } else {
        ESP_LOGE(TAG, "OTA begin failed");
    }
}

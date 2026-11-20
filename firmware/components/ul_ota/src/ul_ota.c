/*
 * OTA from the cmd topic (op "ota"): download over HTTPS into the idle slot,
 * check the version and the SHA-256 of what was written against the command,
 * then switch and restart. Progress goes out as cmd_result with `progress`.
 * The new image stays "pending verify" until it reaches the broker; if it
 * never does, the bootloader rolls back on the next reset.
 */
#include <stdlib.h>
#include <string.h>
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "psa/crypto.h"
#include "ul_core.h"
#include "ul_identity.h"
#include "ul_mqtt.h"
#include "ul_ota.h"

static const char *TAG = "ul_ota";

static volatile bool s_busy;

static bool sha256_matches(const esp_partition_t *p, size_t len, const char *want_hex)
{
    psa_hash_operation_t op = PSA_HASH_OPERATION_INIT;
    if (psa_hash_setup(&op, PSA_ALG_SHA_256) != PSA_SUCCESS) {
        return false;
    }
    uint8_t *buf = malloc(4096);
    bool ok = buf != NULL;
    for (size_t off = 0; ok && off < len; off += 4096) {
        size_t n = len - off < 4096 ? len - off : 4096;
        ok = esp_partition_read(p, off, buf, n) == ESP_OK && psa_hash_update(&op, buf, n) == PSA_SUCCESS;
    }
    free(buf);
    uint8_t digest[32];
    size_t dl = 0;
    if (!ok || psa_hash_finish(&op, digest, sizeof(digest), &dl) != PSA_SUCCESS) {
        psa_hash_abort(&op);
        return false;
    }
    char hex[65];
    for (int i = 0; i < 32; i++) {
        snprintf(hex + i * 2, 3, "%02x", digest[i]);
    }
    return strcmp(hex, want_hex) == 0;
}

static void ota_task(void *arg)
{
    ul_cmd_t *c = arg;
    const char *ca = ul_identity_server_ca();
    esp_http_client_config_t http = {
        .url = c->url, .timeout_ms = 15000, .keep_alive_enable = true,
        .crt_bundle_attach = ca ? NULL : esp_crt_bundle_attach, .cert_pem = ca,
    };
    esp_https_ota_config_t cfg = {.http_config = &http};
    esp_https_ota_handle_t h = NULL;
    const char *fail = NULL;
    ESP_LOGI(TAG, "update to %s from %s", c->version, c->url);
    esp_err_t err = esp_https_ota_begin(&cfg, &h);
    if (err != ESP_OK) {
        fail = "download_failed";
        goto done;
    }
    esp_app_desc_t desc;
    if (esp_https_ota_get_img_desc(h, &desc) != ESP_OK || strcmp(desc.version, c->version) != 0) {
        fail = "version_mismatch";
        goto abort;
    }
    int total = esp_https_ota_get_image_size(h), last = 0;
    while ((err = esp_https_ota_perform(h)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        int read = esp_https_ota_get_image_len_read(h);
        int pct = total > 0 ? read * 100 / total : 0;
        if (pct >= last + 10 && pct < 100) {
            last = pct - pct % 10;
            ul_mqtt_cmd_result(c->id, true, NULL, NULL, last);
        }
    }
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(h)) {
        fail = "download_failed";
        goto abort;
    }
    if (!sha256_matches(esp_ota_get_next_update_partition(NULL), (size_t)esp_https_ota_get_image_len_read(h), c->sha256)) {
        fail = "sha256_mismatch";
        goto abort;
    }
    err = esp_https_ota_finish(h);
    h = NULL;
    if (err != ESP_OK) {
        fail = "image_invalid";
        goto done;
    }
    ESP_LOGI(TAG, "update written, restarting into %s", c->version);
    ul_mqtt_cmd_result(c->id, true, NULL, NULL, -1);
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();

abort:
    esp_https_ota_abort(h);
done:
    ESP_LOGE(TAG, "update failed: %s", fail);
    ul_mqtt_cmd_result(c->id, false, fail, esp_err_to_name(err), -1);
    free(c);
    s_busy = false;
    vTaskDelete(NULL);
}

static esp_err_t on_ota_cmd(const ul_cmd_t *cmd)
{
    if (s_busy) {
        return ESP_ERR_INVALID_STATE;
    }
    ul_cmd_t *c = malloc(sizeof(*c));
    if (!c) {
        return ESP_ERR_NO_MEM;
    }
    *c = *cmd;
    s_busy = true;
    if (xTaskCreate(ota_task, "ul_ota", 8192, c, 5, NULL) != pdPASS) {
        free(c);
        s_busy = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static void on_mqtt_up(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK &&
        st == ESP_OTA_IMG_PENDING_VERIFY) {
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "reached the broker: new firmware marked valid");
    }
}

static esp_err_t ul_ota_init(void)
{
    ul_mqtt_register_cmd(UL_OP_OTA, on_ota_cmd);
    return esp_event_handler_register(UL_EVENT, UL_EV_MQTT_UP, on_mqtt_up, NULL);
}

UL_MODULE_REGISTER(ota, "ul_ota", UL_STAGE_SERVICE, 50, ul_ota_init)

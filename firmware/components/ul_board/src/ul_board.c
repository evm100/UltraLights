#include <stdlib.h>
#include <string.h>
#include "esp_log.h"
#include "esp_partition.h"
#include "ul_board.h"
#include "ul_contract.h"

static const char *TAG = "ul_board";

#define UL_BOARD_SUBTYPE 0x40

static ul_board_desc_t s_board;
static ul_provision_t *s_prov;
static bool s_have_raw, s_valid;
static uint32_t s_crc;
static char s_error[96];
static ul_pin_report_t s_report;

esp_err_t ul_board_load(void)
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, UL_BOARD_SUBTYPE, "ul_board");
    if (!part) {
        snprintf(s_error, sizeof(s_error), "no ul_board partition");
        ESP_LOGE(TAG, "%s", s_error);
        return ESP_OK;
    }
    size_t size = part->size > 4096 ? 4096 : part->size;
    uint8_t *blob = malloc(size);
    s_prov = calloc(1, sizeof(*s_prov));
    if (!blob || !s_prov) {
        free(blob);
        free(s_prov);
        s_prov = NULL;
        snprintf(s_error, sizeof(s_error), "out of memory");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_partition_read(part, 0, blob, size);
    const char *payload;
    size_t len;
    if (err != ESP_OK) {
        snprintf(s_error, sizeof(s_error), "read failed: %s", esp_err_to_name(err));
        goto fail;
    }
    if (ul_board_blob_parse(blob, size, &payload, &len, &s_crc, s_error, sizeof(s_error)) != 0) {
        goto fail;
    }
    if (ul_partition_decode(payload, len, &s_board, s_prov, s_error, sizeof(s_error)) != 0) {
        s_crc = 0;
        goto fail;
    }
    free(blob);
    s_have_raw = true;

    if (s_board.chip != ul_board_running_chip()) {
        snprintf(s_error, sizeof(s_error), "descriptor is for %s, this is %s",
                 ul_chip_name(s_board.chip), ul_chip_name(ul_board_running_chip()));
        ESP_LOGE(TAG, "%s", s_error);
        return ESP_OK;
    }
    ul_board_check_pins(&s_board, &s_report);
    for (int i = 0; i < s_report.n_warn; i++) {
        ESP_LOGW(TAG, "pin warning %s", s_report.warn[i]);
    }
    if (s_report.n_err) {
        size_t o = (size_t)snprintf(s_error, sizeof(s_error), "board rejected:");
        for (int i = 0; i < s_report.n_err && o < sizeof(s_error); i++) {
            o += (size_t)snprintf(s_error + o, sizeof(s_error) - o, " %s", s_report.err[i]);
        }
        ESP_LOGE(TAG, "%s", s_error);
        return ESP_OK;
    }
    s_valid = true;
    s_error[0] = '\0';
    ESP_LOGI(TAG, "board '%s' (%s), %d channels, crc %08lx", s_board.board, ul_chip_name(s_board.chip),
             s_board.n_channels, (unsigned long)s_crc);
    return ESP_OK;

fail:
    ESP_LOGE(TAG, "no usable descriptor: %s", s_error);
    free(blob);
    free(s_prov);
    s_prov = NULL;
    return ESP_OK;
}

const ul_board_desc_t *ul_board_get(void) { return s_valid ? &s_board : NULL; }
const ul_board_desc_t *ul_board_raw(void) { return s_have_raw ? &s_board : NULL; }
const ul_provision_t *ul_board_provision(void) { return s_prov; }
uint32_t ul_board_crc(void) { return s_crc; }
const char *ul_board_error(void) { return s_error[0] ? s_error : NULL; }
const ul_pin_report_t *ul_board_pin_report(void) { return &s_report; }

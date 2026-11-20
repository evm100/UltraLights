#include <string.h>
#include "driver/temperature_sensor.h"
#include "esp_core_dump.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "ul_core.h"
#include "ul_health.h"
#include "ul_wifi.h"

static const char *TAG = "ul_health";

static temperature_sensor_handle_t s_temp;

static const char *reset_reason(void)
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "poweron";
    case ESP_RST_EXT: return "ext";
    case ESP_RST_SW: return "sw";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "int_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "wdt";
    case ESP_RST_DEEPSLEEP: return "deepsleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    case ESP_RST_USB: return "usb";
    case ESP_RST_JTAG: return "jtag";
    case ESP_RST_EFUSE: return "efuse";
    case ESP_RST_PWR_GLITCH: return "pwr_glitch";
    case ESP_RST_CPU_LOCKUP: return "cpu_lockup";
    default: return "unknown";
    }
}

static const char *ota_state(void)
{
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) != ESP_OK) {
        return NULL; /* flashed over USB: no OTA state */
    }
    if (st == ESP_OTA_IMG_PENDING_VERIFY) {
        return "pending_verify";
    }
    return esp_ota_get_last_invalid_partition() ? "rolled_back" : "valid";
}

void ul_health_collect(ul_health_t *h)
{
    memset(h, 0, sizeof(*h));
    h->uptime_s = (uint32_t)(esp_timer_get_time() / 1000000);
    h->heap_free = (uint32_t)esp_get_free_heap_size();
    h->heap_min = (uint32_t)esp_get_minimum_free_heap_size();
    h->rssi = ul_wifi_rssi();
    ul_wifi_ip(h->ip);
    if (!h->ip[0]) {
        strcpy(h->ip, "0.0.0.0");
    }
    h->channel = ul_wifi_channel();
    h->reset_reason = reset_reason();
    h->wifi_reconnects = ul_wifi_reconnects();
    float t;
    if (s_temp && temperature_sensor_get_celsius(s_temp, &t) == ESP_OK) {
        h->has_temp = true;
        h->temp_c = t;
    }
    h->ota = ota_state();
    ul_wifi_dns(h->dns);
}

static esp_err_t ul_health_init(void)
{
    ESP_LOGI(TAG, "reset reason: %s", reset_reason());
    if (esp_core_dump_image_check() == ESP_OK) {
        ESP_LOGW(TAG, "a core dump from an earlier crash is stored in flash");
    }
    temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    if (temperature_sensor_install(&cfg, &s_temp) != ESP_OK || temperature_sensor_enable(s_temp) != ESP_OK) {
        s_temp = NULL;
    }
    return ESP_OK;
}

UL_MODULE_REGISTER(health, "ul_health", UL_STAGE_SERVICE, 15, ul_health_init)

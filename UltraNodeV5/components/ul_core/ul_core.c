#include "ul_core.h"
#include "sdkconfig.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
//#include "esp_sntp.h"
#include <string.h>
#include <time.h>
#include "esp_netif_sntp.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

static const char *TAG = "ul_core";

static char s_node_id[32] = CONFIG_UL_NODE_ID;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT BIT1
#define WIFI_MAX_RETRY 5
#define WIFI_MAX_BACKOFF_MS 30000

const char* ul_core_get_node_id(void) { return s_node_id; }

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    static int retry_num = 0;
    static int backoff_ms = 1000;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        retry_num = 0;
        backoff_ms = 1000;
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (retry_num < WIFI_MAX_RETRY) {
            vTaskDelay(pdMS_TO_TICKS(backoff_ms));
            esp_wifi_connect();
            retry_num++;
            backoff_ms = backoff_ms * 2;
            if (backoff_ms > WIFI_MAX_BACKOFF_MS) backoff_ms = WIFI_MAX_BACKOFF_MS;
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "got ip:" IPSTR, IP2STR(&event->ip_info.ip));
        retry_num = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

void ul_core_wifi_start(void)
{
    s_wifi_event_group = xEventGroupCreate();

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t sta_cfg = {0};
    strncpy((char*)sta_cfg.sta.ssid, CONFIG_UL_WIFI_SSID, sizeof(sta_cfg.sta.ssid)-1);
    strncpy((char*)sta_cfg.sta.password, CONFIG_UL_WIFI_PSK, sizeof(sta_cfg.sta.password)-1);
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_START, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED, &wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
}

bool ul_core_wait_for_ip(TickType_t timeout)
{
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE, timeout);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

bool ul_core_is_connected(void)
{
    if (!s_wifi_event_group) return false;
    EventBits_t bits = xEventGroupGetBits(s_wifi_event_group);
    return (bits & WIFI_CONNECTED_BIT) != 0;
}

static void sntp_sync_task(void *arg)
{
    const TickType_t interval = pdMS_TO_TICKS(CONFIG_UL_SNTP_SYNC_INTERVAL_S * 1000);
    while (1) {
        vTaskDelay(interval);
        while (!ul_core_is_connected()) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        esp_err_t err = esp_netif_sntp_sync();
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "SNTP resync failed: %s", esp_err_to_name(err));
        }
    }
}

void ul_core_sntp_start(void)
{
    setenv("TZ", "PST8PDT,M3.2.0/2,M11.1.0/2", 1); // America/Los_Angeles
    tzset();

    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&config);

    // Wait until time is set (epoch > 1700000000 ~ 2023)
    time_t now = 0;
    struct tm timeinfo = {0};
    int retries = 0;
    const int max_retries = 20;
    while (retries++ < max_retries) {
        time(&now);
        localtime_r(&now, &timeinfo);
        if (now > 1700000000) break;
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    ESP_LOGI(TAG, "Time sync: %ld", now);
    xTaskCreate(sntp_sync_task, "sntp_sync", 2048, NULL, tskIDLE_PRIORITY, NULL);
}

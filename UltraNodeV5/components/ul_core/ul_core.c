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

static const char *TAG = "ul_core";

static char s_node_id[32] = CONFIG_UL_NODE_ID;

const char* ul_core_get_node_id(void) { return s_node_id; }

void ul_core_wifi_start_blocking(void)
{
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    wifi_config_t sta_cfg = {0};
    strncpy((char*)sta_cfg.sta.ssid, CONFIG_UL_WIFI_SSID, sizeof(sta_cfg.sta.ssid)-1);
    strncpy((char*)sta_cfg.sta.password, CONFIG_UL_WIFI_PSK, sizeof(sta_cfg.sta.password)-1);
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    // Keep attempting to connect until we have an IP address
    esp_netif_t* netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    while (true) {
        ESP_LOGI(TAG, "Connecting to WiFi...");
        esp_wifi_connect();

        // Wait up to ~10 seconds for the interface to come up
        for (int i = 0; i < 40; ++i) {
            if (netif && esp_netif_is_netif_up(netif)) {
                ESP_LOGI(TAG, "WiFi up");
                return;
            }
            vTaskDelay(pdMS_TO_TICKS(250));
        }

        ESP_LOGW(TAG, "WiFi connect timeout, retrying");
        esp_wifi_disconnect();
        vTaskDelay(pdMS_TO_TICKS(1000));
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
}

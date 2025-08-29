#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "driver/rmt.h"
#include "led_strip.h"

#define WIFI_SSID "yourssid"
#define WIFI_PASS "yourpass"
#define MQTT_URI  "mqtt://localhost"
#define NODE_ID   "node"
#define LED_STRIP_GPIO 18
#define LED_STRIP_LENGTH 30

static const char *TAG = "lights";
static EventGroupHandle_t s_wifi_event_group;
static const int WIFI_CONNECTED_BIT = BIT0;
static led_strip_t *strip;
static uint8_t last_r = 0, last_g = 0, last_b = 0;
static bool power_on = false;

static void ws_set_color(uint8_t r, uint8_t g, uint8_t b)
{
    for (int i = 0; i < LED_STRIP_LENGTH; i++) {
        led_strip_set_pixel(strip, i, r, g, b);
    }
    led_strip_refresh(strip, 100);
}

static void handle_ws_set(cJSON *root)
{
    cJSON *color = cJSON_GetObjectItem(root, "color");
    if (cJSON_IsArray(color) && cJSON_GetArraySize(color) == 3) {
        last_r = (uint8_t)cJSON_GetArrayItem(color, 0)->valueint;
        last_g = (uint8_t)cJSON_GetArrayItem(color, 1)->valueint;
        last_b = (uint8_t)cJSON_GetArrayItem(color, 2)->valueint;
        if (power_on) {
            ws_set_color(last_r, last_g, last_b);
        }
    }
}

static void handle_ws_power(cJSON *root)
{
    cJSON *on = cJSON_GetObjectItem(root, "on");
    if (cJSON_IsBool(on)) {
        power_on = cJSON_IsTrue(on);
        if (power_on) {
            ws_set_color(last_r, last_g, last_b);
        } else {
            ws_set_color(0, 0, 0);
        }
    }
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;
    switch (event->event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        esp_mqtt_client_subscribe(event->client, "ul/" NODE_ID "/cmd/ws/set", 1);
        esp_mqtt_client_subscribe(event->client, "ul/" NODE_ID "/cmd/ws/power", 1);
        break;
    case MQTT_EVENT_DATA: {
        char topic[event->topic_len + 1];
        memcpy(topic, event->topic, event->topic_len);
        topic[event->topic_len] = '\0';
        char data[event->data_len + 1];
        memcpy(data, event->data, event->data_len);
        data[event->data_len] = '\0';
        cJSON *root = cJSON_Parse(data);
        if (!root) break;
        if (strcmp(topic, "ul/" NODE_ID "/cmd/ws/set") == 0) {
            handle_ws_set(root);
        } else if (strcmp(topic, "ul/" NODE_ID "/cmd/ws/power") == 0) {
            handle_ws_power(root);
        }
        cJSON_Delete(root);
        break; }
    default:
        break;
    }
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                               int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init(void)
{
    s_wifi_event_group = xEventGroupCreate();
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    esp_event_handler_instance_register(WIFI_EVENT,
                                        ESP_EVENT_ANY_ID,
                                        &wifi_event_handler,
                                        NULL,
                                        &instance_any_id);
    esp_event_handler_instance_register(IP_EVENT,
                                        IP_EVENT_STA_GOT_IP,
                                        &wifi_event_handler,
                                        NULL,
                                        &instance_got_ip);
    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT, false, true, portMAX_DELAY);
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    wifi_init();

    rmt_config_t rmt_cfg = RMT_DEFAULT_CONFIG_TX(LED_STRIP_GPIO, RMT_CHANNEL_0);
    rmt_cfg.clk_div = 2;
    ESP_ERROR_CHECK(rmt_config(&rmt_cfg));
    ESP_ERROR_CHECK(rmt_driver_install(rmt_cfg.channel, 0, 0));

    led_strip_config_t strip_config = LED_STRIP_DEFAULT_CONFIG(LED_STRIP_LENGTH, (led_strip_dev_t)rmt_cfg.channel);
    strip = led_strip_new_rmt_ws2812(&strip_config);
    if (!strip) {
        ESP_LOGE(TAG, "Failed to initialize LED strip");
        return;
    }
    led_strip_clear(strip, 100);

    esp_mqtt_client_config_t mqtt_cfg = {
        .uri = MQTT_URI,
    };
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
}

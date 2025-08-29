#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "led_strip.h"
#include <string.h>
#include <stdbool.h>
#include <stdio.h>

#define WIFI_SSID "yourssid"
#define WIFI_PASS "yourpass"
#define MQTT_URI  "mqtt://localhost"
#define NODE_ID   "node"
#define LED_STRIP_GPIO 18
#define LED_STRIP_LENGTH 30

static const char *TAG = "lights";
static EventGroupHandle_t s_wifi_event_group;
static const int WIFI_CONNECTED_BIT = BIT0;
static led_strip_handle_t strip;
static uint8_t last_r = 0, last_g = 0, last_b = 0;
static bool power_on = false;
static uint8_t brightness = 255;

typedef enum {
    EFFECT_SOLID,
    EFFECT_RAINBOW,
} effect_t;

static effect_t current_effect = EFFECT_SOLID;
static TaskHandle_t effect_task = NULL;

static void stop_effect_task(void)
{
    if (effect_task) {
        vTaskDelete(effect_task);
        effect_task = NULL;
    }
}

static void rainbow_task(void *arg)
{
    uint16_t pos = 0;
    while (1) {
        for (int i = 0; i < LED_STRIP_LENGTH; i++) {
            uint8_t wheel = (i * 256 / LED_STRIP_LENGTH + pos) & 255;
            uint8_t r, g, b;
            if (wheel < 85) {
                r = wheel * 3;
                g = 255 - wheel * 3;
                b = 0;
            } else if (wheel < 170) {
                wheel -= 85;
                r = 255 - wheel * 3;
                g = 0;
                b = wheel * 3;
            } else {
                wheel -= 170;
                r = 0;
                g = wheel * 3;
                b = 255 - wheel * 3;
            }
            r = r * brightness / 255;
            g = g * brightness / 255;
            b = b * brightness / 255;
            led_strip_set_pixel(strip, i, r, g, b);
        }
        led_strip_refresh(strip);
        pos++;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

static void start_rainbow(void)
{
    xTaskCreate(rainbow_task, "rainbow", 2048, NULL, 5, &effect_task);
}

static void ws_set_color(uint8_t r, uint8_t g, uint8_t b)
{
    r = r * brightness / 255;
    g = g * brightness / 255;
    b = b * brightness / 255;
    for (int i = 0; i < LED_STRIP_LENGTH; i++) {
        led_strip_set_pixel(strip, i, r, g, b);
    }
    led_strip_refresh(strip);
}

static void handle_ws_set(cJSON *root)
{
    cJSON *b_item = cJSON_GetObjectItem(root, "brightness");
    if (cJSON_IsNumber(b_item)) {
        brightness = (uint8_t)b_item->valueint;
    }

    cJSON *effect = cJSON_GetObjectItem(root, "effect");
    if (cJSON_IsString(effect) && strcmp(effect->valuestring, "rainbow") == 0) {
        current_effect = EFFECT_RAINBOW;
        stop_effect_task();
        if (power_on) {
            start_rainbow();
        }
        return;
    }

    if (cJSON_IsString(effect) && strcmp(effect->valuestring, "solid") == 0) {
        cJSON *hex = cJSON_GetObjectItem(root, "hex");
        if (cJSON_IsString(hex) && strlen(hex->valuestring) == 7) {
            int r, g, b;
            if (sscanf(hex->valuestring + 1, "%02x%02x%02x", &r, &g, &b) == 3) {
                last_r = (uint8_t)r;
                last_g = (uint8_t)g;
                last_b = (uint8_t)b;
            }
        }
        current_effect = EFFECT_SOLID;
        stop_effect_task();
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
            if (current_effect == EFFECT_RAINBOW) {
                start_rainbow();
            } else {
                ws_set_color(last_r, last_g, last_b);
            }
        } else {
            stop_effect_task();
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
    led_strip_config_t strip_config = {
        .strip_gpio_num = LED_STRIP_GPIO,
        .max_leds = LED_STRIP_LENGTH,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &strip));
    led_strip_clear(strip);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = MQTT_URI,
    };
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(client);
}

/*
 * Status LED patterns, driven by ul_core events. A pattern is 16 ticks of
 * 125 ms (2 s), one bit per tick, MSB first.
 *   setup page open      1 s on, 1 s off
 *   joining Wi-Fi        fast blink
 *   no identity          three short blinks
 *   reaching the broker  two short blinks
 *   online               off
 */
#include "driver/gpio.h"
#include "esp_timer.h"
#include "ul_board.h"
#include "ul_core.h"
#include "ul_status.h"

#define P_SETUP      0xFF00
#define P_JOINING    0xAAAA
#define P_NO_ID      0xA800
#define P_BROKER     0xA000
#define P_ONLINE     0x0000

static gpio_num_t s_gpio = GPIO_NUM_NC;
static bool s_active_low;
static volatile uint16_t s_pattern = P_JOINING;
static int s_tick;
static bool s_setup;

static void tick(void *arg)
{
    bool on = (s_pattern >> (15 - s_tick)) & 1;
    s_tick = (s_tick + 1) & 15;
    gpio_set_level(s_gpio, on != s_active_low);
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    switch (id) {
    case UL_EV_SETUP_STARTED:
        s_setup = true;
        s_pattern = P_SETUP;
        break;
    case UL_EV_NET_DOWN:
        s_pattern = s_setup ? P_SETUP : P_JOINING;
        break;
    case UL_EV_NET_UP:
        s_setup = false;
        s_pattern = P_BROKER;
        break;
    case UL_EV_IDENTITY_FAILED:
        s_pattern = P_NO_ID;
        break;
    case UL_EV_MQTT_UP:
        s_pattern = P_ONLINE;
        break;
    case UL_EV_MQTT_DOWN:
        s_pattern = P_BROKER;
        break;
    default:
        break;
    }
}

static esp_err_t ul_status_init(void)
{
    const ul_board_desc_t *b = ul_board_get();
    if (!b || !b->has_status) {
        return ESP_OK;
    }
    s_gpio = (gpio_num_t)b->status_gpio;
    s_active_low = b->status_active_low;
    gpio_set_level(s_gpio, s_active_low);
    gpio_config_t io = {.pin_bit_mask = 1ULL << b->status_gpio, .mode = GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&io));
    esp_timer_handle_t t;
    const esp_timer_create_args_t ta = {.callback = tick, .name = "ul_status"};
    ESP_ERROR_CHECK(esp_timer_create(&ta, &t));
    ESP_ERROR_CHECK(esp_timer_start_periodic(t, 125 * 1000));
    return esp_event_handler_register(UL_EVENT, ESP_EVENT_ANY_ID, on_event, NULL);
}

UL_MODULE_REGISTER(status, "ul_status", UL_STAGE_SERVICE, 0, ul_status_init)

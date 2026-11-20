/*
 * Relay and solenoid coils on plain GPIOs. ul_core decides on/off and enforces
 * a solenoid's max_on_ms; this driver only sets the pin, inactive at boot.
 */
#include "driver/gpio.h"
#include "esp_log.h"
#include "ul_board.h"
#include "ul_core.h"
#include "ul_switch.h"

static const char *TAG = "ul_switch";

typedef struct {
    gpio_num_t gpio;
    bool active_low;
} sw_t;

static sw_t s_sw[UL_MAX_CHANNELS];
static int s_n;

static esp_err_t set_on(void *ctx, bool on)
{
    const sw_t *s = ctx;
    return gpio_set_level(s->gpio, on != s->active_low);
}

static const ul_channel_ops_t OPS = {.set_on = set_on};

static esp_err_t ul_switch_init(void)
{
    const ul_board_desc_t *b = ul_board_get();
    for (int i = 0; i < b->n_channels; i++) {
        const ul_channel_desc_t *d = &b->ch[i];
        if (d->type != UL_CH_SWITCH) {
            continue;
        }
        sw_t *s = &s_sw[s_n];
        s->gpio = (gpio_num_t)d->gpio;
        s->active_low = d->active_low;
        /* Set the inactive level before the pin becomes an output, so it never glitches on. */
        gpio_set_level(s->gpio, s->active_low);
        gpio_config_t io = {.pin_bit_mask = 1ULL << d->gpio, .mode = GPIO_MODE_OUTPUT};
        esp_err_t err = gpio_config(&io);
        if (err != ESP_OK) {
            ul_core_report_error("switch_gpio", d->id, "GPIO%d: %s", d->gpio, esp_err_to_name(err));
            continue;
        }
        gpio_set_level(s->gpio, s->active_low);
        s_n++;
        ESP_ERROR_CHECK(ul_core_register_channel(d->id, &OPS, s));
        ESP_LOGI(TAG, "%s: GPIO%d%s", d->id, d->gpio, d->active_low ? " (active low)" : "");
    }
    return ESP_OK;
}

UL_MODULE_REGISTER(switch, "ul_switch", UL_STAGE_DRIVER, 30, ul_switch_init)

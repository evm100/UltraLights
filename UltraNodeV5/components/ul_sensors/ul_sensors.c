#include "ul_sensors.h"
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "ul_mqtt.h"
#include "esp_rom_sys.h"

static const char* TAG = "ul_sensors";

static volatile int cooldown_s = CONFIG_UL_SENSOR_COOLDOWN_S;
static int64_t pir_until = 0;
static int64_t ultra_until = 0;

static void set_until(volatile int64_t* until_var) {
    *until_var = esp_timer_get_time() + (int64_t)cooldown_s * 1000000LL;
}

static bool is_active(volatile int64_t* until_var) {
    return esp_timer_get_time() < *until_var;
}

static void sensors_task(void*)
{
#if CONFIG_UL_PIR_ENABLED
    gpio_config_t pir_cfg = {
        .pin_bit_mask = 1ULL << CONFIG_UL_PIR_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&pir_cfg);
#endif

#if CONFIG_UL_ULTRA_ENABLED
    gpio_config_t trig = {
        .pin_bit_mask = 1ULL << CONFIG_UL_ULTRA_TRIG_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&trig);
    gpio_config_t echo = {
        .pin_bit_mask = 1ULL << CONFIG_UL_ULTRA_ECHO_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    gpio_config(&echo);
#endif

    while (1) {
#if CONFIG_UL_PIR_ENABLED
        int pir = gpio_get_level(CONFIG_UL_PIR_GPIO);
        if (pir || is_active((int64_t*)&pir_until)) {
            if (pir) set_until(&pir_until);
            ul_mqtt_publish_motion("pir", "MOTION_DETECTED");
        }
#endif

#if CONFIG_UL_ULTRA_ENABLED
        // Simple blocking ping (skeleton): trigger 10us pulse and measure echo
        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 0);
        esp_rom_delay_us(2);
        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 1);
        esp_rom_delay_us(10);
        gpio_set_level(CONFIG_UL_ULTRA_TRIG_GPIO, 0);

        // naive measure loop (timeout ~ 25ms)
        int64_t t0 = esp_timer_get_time();
        while (gpio_get_level(CONFIG_UL_ULTRA_ECHO_GPIO) == 0 && esp_timer_get_time() - t0 < 25000) {}
        int64_t start = esp_timer_get_time();
        while (gpio_get_level(CONFIG_UL_ULTRA_ECHO_GPIO) == 1 && esp_timer_get_time() - start < 25000) {}
        int64_t dur = esp_timer_get_time() - start; // microseconds
        // distance mm ~ dur(us) * 0.343/2 mm/us
        int dist_mm = (int)(dur * 0.1715);

        if (dist_mm > 0 && dist_mm < CONFIG_UL_ULTRA_DISTANCE_MM) {
            set_until(&ultra_until);
        }
        if (is_active((int64_t*)&ultra_until)) {
            ul_mqtt_publish_motion("ultra", "MOTION_NEAR");
        }
#endif
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void ul_sensors_start(void)
{
    // Pin sensor processing to core 0 so core 1 can be dedicated to LED work
    xTaskCreatePinnedToCore(sensors_task, "sensors", 4096, NULL, 5, NULL, 0);
}

void ul_sensors_set_cooldown(int seconds)
{
    if (seconds < 10) seconds = 10;
    if (seconds > 3600) seconds = 3600;
    cooldown_s = seconds;
}


void ul_sensors_get_status(ul_sensor_status_t* out) {
    if (!out) return;
    out->cooldown_s = cooldown_s;
#if CONFIG_UL_PIR_ENABLED
    out->pir_enabled = true;
    out->pir_active = is_active((int64_t*)&pir_until);
#else
    out->pir_enabled = false;
    out->pir_active = false;
#endif
#if CONFIG_UL_ULTRA_ENABLED
    out->ultra_enabled = true;
    out->ultra_active = is_active((int64_t*)&ultra_until);
    out->near_threshold_mm = CONFIG_UL_ULTRA_DISTANCE_MM;
#else
    out->ultra_enabled = false;
    out->ultra_active = false;
    out->near_threshold_mm = 0;
#endif
}

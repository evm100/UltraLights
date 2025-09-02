#include "ul_sensors.h"
#include "sdkconfig.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "ul_mqtt.h"
#include "esp_rom_sys.h"
#include "ul_task.h"
#include "ul_white_engine.h"
#include <string.h>

static const char* TAG = "ul_sensors";

static volatile int pir_motion_time_s = CONFIG_UL_SENSOR_COOLDOWN_S;
static volatile int sonic_motion_time_s = CONFIG_UL_SENSOR_COOLDOWN_S;
static volatile int sonic_threshold_mm = CONFIG_UL_ULTRA_DISTANCE_MM;
static volatile int motion_on_channel = -1;
static int64_t pir_until = 0;
static int64_t ultra_until = 0;
static uint8_t saved_brightness = 0;
static bool brightness_override = false;
static ul_motion_state_t current_state = UL_MOTION_NONE;

typedef struct {
    char ws[160];
    char white[160];
} motion_cmd_t;

// Default commands applied when the motion engine enters a new state. These are
// simple placeholders; the server may overwrite them at runtime via MQTT.
static motion_cmd_t motion_cmds[3] = {
    { // UL_MOTION_NONE
        .ws = "{\"strip\":0,\"effect\":\"solid\",\"brightness\":0,\"speed\":1.0,\"params\":[0,0,0]}",
        .white = "{\"channel\":0,\"effect\":\"solid\",\"brightness\":0}"
    },
    { // UL_MOTION_DETECTED
        .ws = "{\"strip\":0,\"effect\":\"solid\",\"brightness\":50,\"speed\":1.0,\"params\":[255,255,255]}",
        .white = "{\"channel\":0,\"effect\":\"solid\",\"brightness\":50}"
    },
    { // UL_MOTION_NEAR
        .ws = "{\"strip\":0,\"effect\":\"solid\",\"brightness\":100,\"speed\":1.0,\"params\":[255,255,255]}",
        .white = "{\"channel\":0,\"effect\":\"solid\",\"brightness\":100}"
    }
};

static void apply_motion_state(ul_motion_state_t st) {
    if (st == current_state) return;
    current_state = st;
    if (motion_cmds[st].ws[0]) {
        ul_mqtt_run_local("ws/set", motion_cmds[st].ws);
    }
    if (motion_cmds[st].white[0]) {
        ul_mqtt_run_local("white/set", motion_cmds[st].white);
    }
}

static void set_until(volatile int64_t* until_var, int seconds) {
    *until_var = esp_timer_get_time() + (int64_t)seconds * 1000000LL;
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
        bool was_pir = is_active((int64_t*)&pir_until);
        int pir = gpio_get_level(CONFIG_UL_PIR_GPIO);
        if (pir) set_until(&pir_until, pir_motion_time_s);
        bool pir_now = is_active((int64_t*)&pir_until);
        if (pir_now && !was_pir) {
            ul_mqtt_publish_motion("pir", "MOTION_DETECTED");
        } else if (!pir_now && was_pir) {
            ul_mqtt_publish_motion("pir", "MOTION_CLEAR");
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

        bool was_ultra = is_active((int64_t*)&ultra_until);
        if (dist_mm > 0 && dist_mm < sonic_threshold_mm) {
            // Only distances within the configured threshold count as motion.
            // Readings beyond the threshold are ignored entirely.
            set_until(&ultra_until, sonic_motion_time_s);
        }
        bool ultra_now = is_active((int64_t*)&ultra_until);
        if (ultra_now && !was_ultra) {
            ul_mqtt_publish_motion("ultra", "MOTION_NEAR");
        } else if (!ultra_now && was_ultra) {
            ul_mqtt_publish_motion("ultra", "MOTION_FAR");
        }
#endif

        bool pir_active = false;
        bool ultra_active = false;
#if CONFIG_UL_PIR_ENABLED
        pir_active = is_active((int64_t*)&pir_until);
#endif
#if CONFIG_UL_ULTRA_ENABLED
        ultra_active = is_active((int64_t*)&ultra_until);
#endif

        ul_motion_state_t new_state = UL_MOTION_NONE;
        if (pir_active) new_state = UL_MOTION_DETECTED;
        if (ultra_active) new_state = UL_MOTION_NEAR; // Near overrides PIR motion
        apply_motion_state(new_state);

        bool active = pir_active || ultra_active;
        if (motion_on_channel >= 0) {
            if (active && !brightness_override) {
                ul_white_ch_status_t st;
                if (ul_white_get_status(motion_on_channel, &st)) {
                    saved_brightness = st.brightness;
                    ul_white_set_brightness(motion_on_channel, 255);
                    brightness_override = true;
                }
            } else if (!active && brightness_override) {
                ul_white_set_brightness(motion_on_channel, saved_brightness);
                brightness_override = false;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(CONFIG_UL_SENSOR_POLL_MS));
    }
}

void ul_sensors_start(void)
{
    // Sensor processing pinned to core 0 when multiple cores are present
    ul_task_create(sensors_task, "sensors", 4096, NULL, 5, NULL, 0);
}

void ul_sensors_set_cooldown(int seconds)
{
    if (seconds < 1) seconds = 1;
    if (seconds > 3600) seconds = 3600;
    pir_motion_time_s = sonic_motion_time_s = seconds;
}

void ul_sensors_set_pir_motion_time(int seconds)
{
    if (seconds < 1) seconds = 1;
    if (seconds > 3600) seconds = 3600;
    pir_motion_time_s = seconds;
}

void ul_sensors_set_sonic_motion_time(int seconds)
{
    if (seconds < 1) seconds = 1;
    if (seconds > 3600) seconds = 3600;
    sonic_motion_time_s = seconds;
}

void ul_sensors_set_sonic_threshold_mm(int mm)
{
    if (mm < 50) mm = 50;
    if (mm > 4000) mm = 4000;
    sonic_threshold_mm = mm;
}

void ul_sensors_set_motion_on_channel(int ch)
{
    if (ch < 0 || ch > 3) ch = -1;
    motion_on_channel = ch;
}

void ul_sensors_set_motion_command(ul_motion_state_t state, const char* ws_cmd, const char* white_cmd)
{
    if (state < UL_MOTION_NONE || state > UL_MOTION_NEAR) return;
    if (ws_cmd) {
        strncpy(motion_cmds[state].ws, ws_cmd, sizeof(motion_cmds[state].ws)-1);
        motion_cmds[state].ws[sizeof(motion_cmds[state].ws)-1] = '\0';
    }
    if (white_cmd) {
        strncpy(motion_cmds[state].white, white_cmd, sizeof(motion_cmds[state].white)-1);
        motion_cmds[state].white[sizeof(motion_cmds[state].white)-1] = '\0';
    }
}


void ul_sensors_get_status(ul_sensor_status_t* out) {
    if (!out) return;
    out->pir_motion_time_s = pir_motion_time_s;
    out->sonic_motion_time_s = sonic_motion_time_s;
    out->sonic_threshold_mm = sonic_threshold_mm;
    out->motion_on_channel = motion_on_channel;
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
#else
    out->ultra_enabled = false;
    out->ultra_active = false;
#endif
    out->motion_state = current_state;
}

#include "ul_ws_engine.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "ul_task.h"
#include "ul_core.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "led_strip.h"
#include "led_strip_types.h"
#if CONFIG_UL_WS_BACKEND_SPI
#include "driver/spi_master.h"
#endif
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "cJSON.h"
#include "effects_ws/effect.h"
#include "ul_common_effects.h"

static const char* TAG = "ul_ws";

typedef struct {
    const ws_effect_t* eff;
    uint8_t solid_r, solid_g, solid_b;
    uint8_t brightness; // 0..255
    float speed;
    float frame_pos;
    int pixels;
    led_strip_handle_t handle;
    uint8_t* frame; // rgb * pixels
} ws_strip_t;

static ws_strip_t s_strips[2];
static int s_current_strip_idx = 0;
static SemaphoreHandle_t s_refresh_sem;
static TaskHandle_t s_refresh_task = NULL;
static TaskHandle_t s_ws_task = NULL;

int ul_ws_effect_current_strip(void) { return s_current_strip_idx; }

static ws_strip_t* get_strip(int idx);

void ul_ws_apply_json(cJSON* root) {
    if (!root) return;
    int strip = 0;
    cJSON* jstrip = cJSON_GetObjectItem(root, "strip");
    if (jstrip && cJSON_IsNumber(jstrip)) strip = jstrip->valueint;

    cJSON* jbri = cJSON_GetObjectItem(root, "brightness");
    if (jbri && cJSON_IsNumber(jbri)) {
        int bri = jbri->valueint;
        if (bri < 0) bri = 0;
        if (bri > 255) bri = 255;
        ul_ws_set_brightness(strip, (uint8_t)bri);
    }

    cJSON* jspeed = cJSON_GetObjectItem(root, "speed");
    if (jspeed && cJSON_IsNumber(jspeed)) {
        ws_strip_t* s = get_strip(strip);
        if (s) s->speed = (float)jspeed->valuedouble;
    }

    const char* effect = NULL;
    cJSON* jeffect = cJSON_GetObjectItem(root, "effect");
    if (jeffect && cJSON_IsString(jeffect)) {
        effect = jeffect->valuestring;
        if (strip < 0 || strip > 1 || s_strips[strip].pixels <= 0) {
            ESP_LOGW(TAG, "Effect %s requested on disabled strip %d", effect, strip);
            effect = NULL;
        } else if (!ul_ws_set_effect(strip, effect)) {
            ESP_LOGW(TAG, "Unknown effect: %s", effect);
            effect = NULL;
        }
    }

    cJSON* jparams = cJSON_GetObjectItem(root, "params");
    if (effect && jparams && cJSON_IsArray(jparams)) {
        const ws_effect_t* eff = s_strips[strip].eff;
        if (eff && eff->apply_params) {
            eff->apply_params(strip, jparams);
        }
    }
}

static const ws_effect_t* find_effect_by_name(const char* name) {
    int n=0;
    const ws_effect_t* tbl = ul_ws_get_effects(&n);
    for (int i=0;i<n;i++) {
        if (strcmp(tbl[i].name, name)==0) return &tbl[i];
    }
    return NULL;
}

static void init_strip(int idx, int gpio, int pixels, bool enabled) {
    if (!enabled) {
        s_strips[idx].pixels = 0;
        s_strips[idx].handle = NULL;
        s_strips[idx].frame = NULL;
        return;
    }

    led_strip_config_t strip_config = {
        .strip_gpio_num = gpio,
        .max_leds = pixels,
        .led_model = LED_MODEL_WS2812,
        .flags.invert_out = false,
    };

#if CONFIG_UL_WS_BACKEND_SPI
    led_strip_spi_config_t backend_config = {
        .clk_src = SPI_CLK_SRC_DEFAULT,
        .spi_bus =
#if CONFIG_UL_IS_ESP32C3
            SPI2_HOST,
#else
            (idx == 0 ? SPI2_HOST : SPI3_HOST),
#endif
        .flags = {
            .with_dma = CONFIG_UL_WS_SPI_USE_DMA,
        },
    };
    ESP_ERROR_CHECK(led_strip_new_spi_device(&strip_config, &backend_config,
                                             &s_strips[idx].handle));
#elif CONFIG_UL_WS_BACKEND_RMT
    led_strip_rmt_config_t backend_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 0,
        .flags = {
            .with_dma = 0,
        },
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &backend_config,
                                             &s_strips[idx].handle));
#else
#error "Unsupported WS backend configuration"
#endif

    led_strip_clear(s_strips[idx].handle);
    s_strips[idx].pixels = pixels;
    s_strips[idx].frame = (uint8_t *)heap_caps_calloc(pixels * 3, sizeof(uint8_t),
                                                      MALLOC_CAP_8BIT);
    if (!s_strips[idx].frame) {
        ESP_LOGE(TAG, "Failed to allocate frame buffer for strip %d", idx);
        led_strip_del(s_strips[idx].handle);
        s_strips[idx].handle = NULL;
        s_strips[idx].pixels = 0;
        return;
    }
    // defaults
    int n=0; const ws_effect_t* tbl = ul_ws_get_effects(&n);
    s_strips[idx].eff = &tbl[0]; // solid
    s_strips[idx].solid_r = s_strips[idx].solid_g = s_strips[idx].solid_b = 0;
    s_strips[idx].brightness = 255;
    s_strips[idx].speed = 1.0f;
    s_strips[idx].frame_pos = 0.0f;
}

static void apply_brightness(uint8_t* f, int count, uint8_t bri) {
    if (bri == 255) return;
    for (int i=0;i<count;i++) {
        int v = (f[i] * bri) / 255;
        f[i] = (uint8_t)v;
    }
}

static void render_one(ws_strip_t* s, int idx) {
    if (!s->pixels || !s->handle) return;
    s_current_strip_idx = idx;
    // Produce frame
    memset(s->frame, 0, s->pixels*3);
    if (s->eff && s->eff->render) {
        s->frame_pos += s->speed;
        int frame_idx = (int)s->frame_pos;
        s->eff->render(s->frame, s->pixels, frame_idx);
    }
#if CONFIG_UL_GAMMA_ENABLE
    for (int i=0;i<s->pixels;i++) {
        s->frame[3*i+0] = ul_gamma8(s->frame[3*i+0]);
        s->frame[3*i+1] = ul_gamma8(s->frame[3*i+1]);
        s->frame[3*i+2] = ul_gamma8(s->frame[3*i+2]);
    }
#endif
    apply_brightness(s->frame, s->pixels*3, s->brightness);
    // Push to device
    for (int i=0;i<s->pixels;i++) {
        led_strip_set_pixel(s->handle, i, s->frame[3*i+0], s->frame[3*i+1], s->frame[3*i+2]);
    }
}

static void ws_task(void*)
{
    const TickType_t period_ticks = pdMS_TO_TICKS(1000 / CONFIG_UL_WS2812_FPS);
    TickType_t last_wake = xTaskGetTickCount();

    while (1) {
#if CONFIG_UL_WS0_ENABLED
        render_one(&s_strips[0], 0);
#endif
#if CONFIG_UL_WS1_ENABLED
        render_one(&s_strips[1], 1);
#endif
        if (s_refresh_sem) xSemaphoreGive(s_refresh_sem);
        vTaskDelayUntil(&last_wake, period_ticks);
    }
}

static void led_refresh_task(void *arg) {
    while (1) {
        xSemaphoreTake(s_refresh_sem, portMAX_DELAY);
#if CONFIG_UL_WS0_ENABLED
        if (s_strips[0].handle) led_strip_refresh(s_strips[0].handle);
#endif
#if CONFIG_UL_WS1_ENABLED
        if (s_strips[1].handle) led_strip_refresh(s_strips[1].handle);
#endif
    }
}

void ul_ws_engine_start(void)
{
    if (!ul_core_is_connected()) {
        ESP_LOGW(TAG, "Network not connected; WS engine not started");
        return;
    }
#if CONFIG_UL_WS0_ENABLED
    init_strip(0, CONFIG_UL_WS0_GPIO, CONFIG_UL_WS0_PIXELS, true);
#else
    init_strip(0, 0, 0, false);
#endif
#if CONFIG_UL_WS1_ENABLED
    init_strip(1, CONFIG_UL_WS1_GPIO, CONFIG_UL_WS1_PIXELS, true);
#else
    init_strip(1, 0, 0, false);
#endif
    s_refresh_sem = xSemaphoreCreateBinary();
    // Pixel refresh tasks pin to core 1 on multi-core targets to free core 0
    // for networking and other work.
    ul_task_create(led_refresh_task, "ws_refresh", 2048, NULL, 24, &s_refresh_task, 1);
    ul_task_create(ws_task, "ws60fps", 6144, NULL, 23, &s_ws_task, 1);
    if (s_refresh_sem) xSemaphoreGive(s_refresh_sem);
}

void ul_ws_engine_stop(void)
{
    if (s_refresh_task) {
        vTaskDelete(s_refresh_task);
        s_refresh_task = NULL;
    }
    if (s_ws_task) {
        vTaskDelete(s_ws_task);
        s_ws_task = NULL;
    }
    for (int i = 0; i < 2; ++i) {
        if (s_strips[i].handle) {
            led_strip_del(s_strips[i].handle);
            s_strips[i].handle = NULL;
        }
        free(s_strips[i].frame);
        s_strips[i].frame = NULL;
        s_strips[i].pixels = 0;
    }
    if (s_refresh_sem) {
        vSemaphoreDelete(s_refresh_sem);
        s_refresh_sem = NULL;
    }
}


bool ul_ws_hex_to_rgb(const char* hex, uint8_t* r, uint8_t* g, uint8_t* b) {
    if (!hex || !r || !g || !b) return false;
    if (hex[0] == '#') hex++;
    if (strlen(hex) != 6) return false;
    for (int i = 0; i < 6; ++i) {
        if (!isxdigit((unsigned char)hex[i])) return false;
    }
    char buf[3] = {0};
    buf[2] = 0;
    buf[0] = hex[0]; buf[1] = hex[1]; *r = (uint8_t)strtol(buf, NULL, 16);
    buf[0] = hex[2]; buf[1] = hex[3]; *g = (uint8_t)strtol(buf, NULL, 16);
    buf[0] = hex[4]; buf[1] = hex[5]; *b = (uint8_t)strtol(buf, NULL, 16);
    return true;
}

// ---- Control & Status API ----

static ws_strip_t* get_strip(int idx) {
    if (idx < 0 || idx > 1) return NULL;
    if (s_strips[idx].pixels <= 0) return NULL;
    return &s_strips[idx];
}

bool ul_ws_set_effect(int strip, const char* name) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return false;
    const ws_effect_t* e = find_effect_by_name(name);
    if (!e) return false;
    s->eff = e;
    s->frame_pos = 0.0f;
    if (s->eff->init) s->eff->init();
    return true;
}

void ul_ws_set_solid_rgb(int strip, uint8_t r, uint8_t g, uint8_t b) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return;
    s->solid_r = r; s->solid_g = g; s->solid_b = b;
}

void ul_ws_get_solid_rgb(int strip, uint8_t* r, uint8_t* g, uint8_t* b) {
    ws_strip_t* s = get_strip(strip);
    if (!s || !r || !g || !b) return;
    *r = s->solid_r; *g = s->solid_g; *b = s->solid_b;
}

void ul_ws_set_brightness(int strip, uint8_t bri) {
    ws_strip_t* s = get_strip(strip);
    if (!s) return;
    s->brightness = bri;
}

int ul_ws_get_strip_count(void) {
    int n=0;
#if CONFIG_UL_WS0_ENABLED
    if (s_strips[0].pixels>0) n++;
#endif
#if CONFIG_UL_WS1_ENABLED
    if (s_strips[1].pixels>0) n++;
#endif
    return n;
}

bool ul_ws_get_status(int idx, ul_ws_strip_status_t* out) {
    if (!out) return false;
    ws_strip_t* s = get_strip(idx);
    if (!s) { memset(out,0,sizeof(*out)); return false; }
    out->enabled = true;
    out->brightness = s->brightness;
    out->pixels = s->pixels;
    out->gpio = 0; // not tracked in led_strip
    out->fps = CONFIG_UL_WS2812_FPS;
    strncpy(out->effect, s->eff ? s->eff->name : "unknown", sizeof(out->effect)-1);
    out->effect[sizeof(out->effect)-1]=0;
    out->color[0]=s->solid_r; out->color[1]=s->solid_g; out->color[2]=s->solid_b;
    return true;
}

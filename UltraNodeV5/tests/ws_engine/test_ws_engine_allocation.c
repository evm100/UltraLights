#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "ul_task.h"
#include "ul_core.h"
#include "ul_common_effects.h"
#include "effects_ws/effect.h"
#include "ul_ws_engine.h"

// ---- Test stubs & state ---------------------------------------------------

static int g_heap_caps_malloc_call_count = 0;
static int g_heap_caps_malloc_fail_call = -1;
static size_t g_heap_caps_malloc_last_size = 0;

static led_strip_stub_t g_led_strip_storage[4];
static int g_led_strip_storage_count = 0;
static int g_led_strip_del_calls = 0;
static int g_led_strip_set_pixel_total = 0;
static int g_led_strip_refresh_total = 0;

static TickType_t g_tick_count = 0;

typedef struct {
    bool given;
    bool deleted;
    int give_count;
    int take_count;
} stub_semaphore_t;

static stub_semaphore_t g_semaphores[4];
static int g_semaphore_count = 0;

static bool g_core_connected = true;

static int g_task_create_calls = 0;

static int g_effect_render_calls = 0;
static int g_effect_init_calls = 0;

// Forward declarations for helpers
static void reset_test_state(void);
static void test_set_heap_caps_malloc_fail_call(int call);

// ---- Stub implementations -------------------------------------------------

void test_set_heap_caps_malloc_fail_call(int call) {
    g_heap_caps_malloc_fail_call = call;
    g_heap_caps_malloc_call_count = 0;
}

void* heap_caps_malloc(size_t size, uint32_t caps) {
    (void)caps;
    g_heap_caps_malloc_call_count++;
    g_heap_caps_malloc_last_size = size;
    if (g_heap_caps_malloc_fail_call > 0 &&
        g_heap_caps_malloc_call_count == g_heap_caps_malloc_fail_call) {
        return NULL;
    }
    return calloc(1, size);
}

esp_err_t led_strip_new_spi_device(const led_strip_config_t* config,
                                   const led_strip_spi_config_t* spi_config,
                                   led_strip_handle_t* out_handle) {
    (void)config;
    (void)spi_config;
    if (!out_handle) {
        return -1;
    }
    led_strip_stub_t* stub = &g_led_strip_storage[g_led_strip_storage_count++];
    memset(stub, 0, sizeof(*stub));
    stub->id = g_led_strip_storage_count;
    *out_handle = stub;
    return ESP_OK;
}

esp_err_t led_strip_clear(led_strip_handle_t handle) {
    if (handle) {
        handle->cleared = true;
    }
    return ESP_OK;
}

esp_err_t led_strip_set_pixel(led_strip_handle_t handle, int index, uint32_t red, uint32_t green, uint32_t blue) {
    (void)index;
    (void)red;
    (void)green;
    (void)blue;
    if (handle) {
        handle->set_pixel_calls++;
    }
    g_led_strip_set_pixel_total++;
    return ESP_OK;
}

esp_err_t led_strip_refresh(led_strip_handle_t handle) {
    if (handle) {
        handle->refresh_calls++;
    }
    g_led_strip_refresh_total++;
    return ESP_OK;
}

esp_err_t led_strip_del(led_strip_handle_t handle) {
    if (handle) {
        handle->deleted = true;
    }
    g_led_strip_del_calls++;
    return ESP_OK;
}

TickType_t xTaskGetTickCount(void) {
    return g_tick_count++;
}

void vTaskDelayUntil(TickType_t* const pxPreviousWakeTime, TickType_t xTimeIncrement) {
    if (pxPreviousWakeTime) {
        *pxPreviousWakeTime += xTimeIncrement;
    }
}

void vTaskDelete(TaskHandle_t task) {
    (void)task;
}

SemaphoreHandle_t xSemaphoreCreateBinary(void) {
    stub_semaphore_t* sem = &g_semaphores[g_semaphore_count++];
    memset(sem, 0, sizeof(*sem));
    return sem;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) {
    (void)ticks;
    if (!sem) return pdFALSE;
    stub_semaphore_t* stub = (stub_semaphore_t*)sem;
    stub->take_count++;
    if (stub->given) {
        stub->given = false;
        return pdTRUE;
    }
    return pdFALSE;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t sem) {
    if (!sem) return pdFALSE;
    stub_semaphore_t* stub = (stub_semaphore_t*)sem;
    stub->given = true;
    stub->give_count++;
    return pdTRUE;
}

void vSemaphoreDelete(SemaphoreHandle_t sem) {
    if (!sem) return;
    stub_semaphore_t* stub = (stub_semaphore_t*)sem;
    stub->deleted = true;
}

BaseType_t ul_task_create(TaskFunction_t task_func,
                          const char *name,
                          const uint32_t stack_depth,
                          void *params,
                          UBaseType_t priority,
                          TaskHandle_t *task_handle,
                          BaseType_t core_id) {
    (void)name;
    (void)stack_depth;
    (void)params;
    (void)priority;
    (void)core_id;
    g_task_create_calls++;
    if (task_handle) {
        *task_handle = (TaskHandle_t)task_func;
    }
    return pdPASS;
}

bool ul_core_is_connected(void) {
    return g_core_connected;
}

uint8_t ul_gamma8(uint8_t x) {
    return x;
}

static void stub_effect_init(void) {
    g_effect_init_calls++;
}

static void stub_effect_render(uint8_t* frame_rgb, int pixels, int frame_idx) {
    g_effect_render_calls++;
    for (int i = 0; i < pixels; ++i) {
        frame_rgb[3*i + 0] = (uint8_t)(frame_idx + 1);
        frame_rgb[3*i + 1] = (uint8_t)(i + 1);
        frame_rgb[3*i + 2] = 0xFF;
    }
}

static const ws_effect_t g_effects[] = {
    {
        .name = "solid",
        .tier = WS_EFFECT_TIER_STANDARD,
        .init = stub_effect_init,
        .render = stub_effect_render,
        .apply_params = NULL,
    },
};

const ws_effect_t* ul_ws_get_effects(int* count) {
    if (count) {
        *count = (int)(sizeof(g_effects) / sizeof(g_effects[0]));
    }
    return g_effects;
}

// ---- Include implementation under test -----------------------------------

#include "../../components/ul_ws_engine/ul_ws_engine.c"

// ---- Helpers --------------------------------------------------------------

static void reset_test_state(void) {
    memset(g_led_strip_storage, 0, sizeof(g_led_strip_storage));
    g_led_strip_storage_count = 0;
    g_led_strip_del_calls = 0;
    g_led_strip_set_pixel_total = 0;
    g_led_strip_refresh_total = 0;
    g_heap_caps_malloc_call_count = 0;
    g_heap_caps_malloc_fail_call = -1;
    g_heap_caps_malloc_last_size = 0;
    g_tick_count = 0;
    memset(g_semaphores, 0, sizeof(g_semaphores));
    g_semaphore_count = 0;
    g_core_connected = true;
    g_task_create_calls = 0;
    g_effect_render_calls = 0;
    g_effect_init_calls = 0;
    ul_ws_engine_stop();
}

static void assert_strip_disabled(int idx) {
    assert(s_strips[idx].pixels == 0);
    assert(s_strips[idx].frame == NULL);
    assert(s_strips[idx].handle == NULL);
    assert(s_strips[idx].eff == NULL);
}

static void assert_strip_enabled(int idx, int expected_pixels) {
    assert(s_strips[idx].pixels == expected_pixels);
    assert(s_strips[idx].frame != NULL);
    assert(s_strips[idx].handle != NULL);
    assert(s_strips[idx].eff != NULL);
}

// ---- Tests ----------------------------------------------------------------

static void test_allocation_failure_second_strip(void) {
    reset_test_state();
    test_set_heap_caps_malloc_fail_call(2);

    ul_ws_engine_start();

    assert_strip_enabled(0, CONFIG_UL_WS0_PIXELS);
    assert_strip_disabled(1);
    assert(g_led_strip_del_calls == 1);
    assert(g_heap_caps_malloc_call_count == 2);
    assert(g_heap_caps_malloc_last_size == (size_t)(CONFIG_UL_WS1_PIXELS * 3));
    assert(ul_ws_get_strip_count() == 1);

    ul_ws_strip_status_t status = {0};
    assert(ul_ws_get_status(0, &status));
    assert(status.enabled);
    assert(status.pixels == CONFIG_UL_WS0_PIXELS);

    memset(&status, 0xAA, sizeof(status));
    assert(!ul_ws_get_status(1, &status));
    // status should have been cleared by ul_ws_get_status when strip disabled
    for (size_t i = 0; i < sizeof(status); ++i) {
        assert(((uint8_t*)&status)[i] == 0);
    }

    int pixel_calls_before = g_led_strip_set_pixel_total;
    render_one(&s_strips[0], 0);
    assert(g_led_strip_set_pixel_total == pixel_calls_before + CONFIG_UL_WS0_PIXELS);
    assert(g_effect_render_calls >= 1);

    pixel_calls_before = g_led_strip_set_pixel_total;
    render_one(&s_strips[1], 1);
    assert(g_led_strip_set_pixel_total == pixel_calls_before);
}

int main(void) {
    test_allocation_failure_second_strip();
    ul_ws_engine_stop();
    printf("All tests passed\n");
    return 0;
}

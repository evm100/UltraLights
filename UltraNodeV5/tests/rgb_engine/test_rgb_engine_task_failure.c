#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/ledc.h"
#include "ul_task.h"
#include "ul_common_effects.h"
#include "effects_rgb/effect.h"
#include "ul_health.h"

// ---- Test state ------------------------------------------------------------

static int g_ledc_timer_config_calls;
static int g_ledc_channel_config_calls;
static int g_ledc_set_duty_calls;
static int g_ledc_update_duty_calls;

static TickType_t g_fake_tick;

static int g_task_create_calls;
static bool g_task_create_should_fail;

static int g_rgb_failure_reports;
static int g_rgb_ok_reports;

static int g_effect_init_calls;
static int g_effect_render_calls;

// ---- Stub implementations --------------------------------------------------

esp_err_t ledc_timer_config(const ledc_timer_config_t* config) {
    (void)config;
    g_ledc_timer_config_calls++;
    return ESP_OK;
}

esp_err_t ledc_channel_config(const ledc_channel_config_t* config) {
    (void)config;
    g_ledc_channel_config_calls++;
    return ESP_OK;
}

esp_err_t ledc_set_duty(ledc_mode_t mode, ledc_channel_t channel, int duty) {
    (void)mode;
    (void)channel;
    (void)duty;
    g_ledc_set_duty_calls++;
    return ESP_OK;
}

esp_err_t ledc_update_duty(ledc_mode_t mode, ledc_channel_t channel) {
    (void)mode;
    (void)channel;
    g_ledc_update_duty_calls++;
    return ESP_OK;
}

TickType_t xTaskGetTickCount(void) { return g_fake_tick++; }

void vTaskDelayUntil(TickType_t* const pxPreviousWakeTime, TickType_t xTimeIncrement) {
    if (pxPreviousWakeTime) {
        *pxPreviousWakeTime += xTimeIncrement;
    }
}

void vTaskDelay(TickType_t ticks) { (void)ticks; }

void vTaskDelete(TaskHandle_t task) { (void)task; }

BaseType_t ul_task_create(TaskFunction_t task_func,
                          const char* name,
                          const uint32_t stack_depth,
                          void* params,
                          UBaseType_t priority,
                          TaskHandle_t* task_handle,
                          BaseType_t core_id) {
    (void)task_func;
    (void)name;
    (void)stack_depth;
    (void)params;
    (void)priority;
    (void)core_id;
    g_task_create_calls++;
    if (g_task_create_should_fail) {
        if (task_handle) {
            *task_handle = NULL;
        }
        return pdFAIL;
    }
    if (task_handle) {
        *task_handle = (TaskHandle_t)0x1;
    }
    return pdPASS;
}

uint8_t ul_gamma8(uint8_t x) { return x; }

static void test_effect_init(void) { g_effect_init_calls++; }

static void test_effect_render(int strip, uint8_t out_rgb[3], int frame_idx) {
    (void)strip;
    (void)frame_idx;
    g_effect_render_calls++;
    out_rgb[0] = 1;
    out_rgb[1] = 2;
    out_rgb[2] = 3;
}

static const rgb_effect_t g_effects[] = {
    {
        .name = "solid",
        .init = test_effect_init,
        .render = test_effect_render,
        .apply_params = NULL,
    },
};

const rgb_effect_t* ul_rgb_get_effects(int* count) {
    if (count) {
        *count = (int)(sizeof(g_effects) / sizeof(g_effects[0]));
    }
    return g_effects;
}

void ul_health_notify_rgb_engine_ok(void) { g_rgb_ok_reports++; }

void ul_health_notify_rgb_engine_failure(void) { g_rgb_failure_reports++; }

// ---- Include implementation under test ------------------------------------

#include "../../components/ul_rgb_engine/ul_rgb_engine.c"

// ---- Helpers ----------------------------------------------------------------

static void reset_test_state(void) {
    ul_rgb_engine_stop();
    memset(s_strips, 0, sizeof(s_strips));
    s_strip_count = 0;
    s_rgb_task = NULL;
    s_current_strip = 0;
    g_ledc_timer_config_calls = 0;
    g_ledc_channel_config_calls = 0;
    g_ledc_set_duty_calls = 0;
    g_ledc_update_duty_calls = 0;
    g_fake_tick = 0;
    g_task_create_calls = 0;
    g_task_create_should_fail = false;
    g_rgb_failure_reports = 0;
    g_rgb_ok_reports = 0;
    g_effect_init_calls = 0;
    g_effect_render_calls = 0;
}

static void assert_strip_disabled(int idx) {
    assert(!s_strips[idx].enabled);
    for (int c = 0; c < 3; ++c) {
        assert(!s_strips[idx].channel[c].configured);
        assert(s_strips[idx].channel[c].gpio == 0);
    }
}

// ---- Tests ------------------------------------------------------------------

static void test_rgb_task_create_failure_unwinds(void) {
    reset_test_state();
    g_task_create_should_fail = true;

    ul_rgb_engine_start();

    assert(g_task_create_calls == 1);
    assert(s_rgb_task == NULL);
    assert(s_strip_count == 0);
    assert_strip_disabled(0);
    assert(g_rgb_failure_reports == 1);
    assert(g_rgb_ok_reports == 0);

    g_task_create_should_fail = false;
    g_task_create_calls = 0;

    ul_rgb_engine_start();

    assert(g_task_create_calls == 1);
    assert(s_rgb_task != NULL);
    assert(s_strip_count == 1);
    assert(s_strips[0].enabled);
    assert(g_rgb_ok_reports == 1);

    ul_rgb_engine_stop();
    assert(s_rgb_task == NULL);
    assert(s_strip_count == 0);
}

int main(void) {
    test_rgb_task_create_failure_unwinds();
    printf("All tests passed\n");
    return 0;
}

/*
 * ul_core: the device model. Owns the outputs, their state and the boot
 * sequence. Drivers register channels with it; services change outputs only
 * through it. See docs/blueprint.html#firmware.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "esp_event.h"
#include "ul_core_logic.h"
#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---------- events (posted to the default event loop) ---------- */

ESP_EVENT_DECLARE_BASE(UL_EVENT);

typedef enum {
    UL_EV_STATE,            /* data: int, output index whose state changed */
    UL_EV_INFO,             /* outputs or config changed: republish info */
    UL_EV_NET_UP,           /* got an IP in station mode */
    UL_EV_NET_DOWN,
    UL_EV_SETUP_STARTED,    /* setup access point is up */
    UL_EV_IDENTITY_READY,   /* certificate and key are available */
    UL_EV_IDENTITY_FAILED,  /* data: ul_error_t */
    UL_EV_MQTT_UP,
    UL_EV_MQTT_DOWN,
    UL_EV_BUTTON,           /* data: ul_btn_action_t */
    UL_EV_ERROR,            /* data: ul_error_t, published as an evt error */
} ul_event_id_t;

typedef struct {
    char code[33];
    char detail[128];
    char out[UL_KEY_SZ];
} ul_error_t;

/* Post UL_EV_ERROR (also logged). out may be NULL. */
void ul_core_report_error(const char *code, const char *out, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));

/* ---------- modules ---------- */

typedef enum { UL_STAGE_DRIVER = 0, UL_STAGE_SERVICE = 1 } ul_stage_t;

typedef struct {
    const char *name;
    ul_stage_t stage;
    int order;               /* lower runs first within a stage */
    esp_err_t (*init)(void);
} ul_module_t;

void ul_module_register(const ul_module_t *m);

/*
 * Register a module from its own component. The component must be built with
 * WHOLE_ARCHIVE so the constructor is linked. main never lists modules.
 */
#define UL_MODULE_REGISTER(id_, name_, stage_, order_, fn_)                         \
    static const ul_module_t ul_module_##id_ = {name_, stage_, order_, fn_};       \
    __attribute__((constructor)) static void ul_module_ctor_##id_(void)             \
    {                                                                               \
        ul_module_register(&ul_module_##id_);                                       \
    }

/* ---------- channels (drivers) ---------- */

typedef struct {
    /*
     * pwm: move to the last of n duty values (0..1 light output), passing
     * through the others at equal intervals over fade_ms. n >= 1.
     */
    esp_err_t (*set_duty)(void *ctx, const float *points, int n, uint32_t fade_ms);
    /* switch: energize or release. */
    esp_err_t (*set_on)(void *ctx, bool on);
    /* argb: like set_duty, with linear r, g, b, w per point, on the first `pixels` pixels. */
    esp_err_t (*set_rgbw)(void *ctx, const float (*points)[4], int n, uint16_t pixels, uint32_t fade_ms);
    /*
     * servo: start one full button press (press, hold, back to rest, settle) and
     * return at once. The driver calls ul_core_press_done when the arm is back at
     * rest. ul_core never starts a press while one is running on that channel.
     */
    esp_err_t (*press)(void *ctx);
} ul_channel_ops_t;

esp_err_t ul_core_register_channel(const char *id, const ul_channel_ops_t *ops, void *ctx);

/* A press started with ops->press has finished (ok = false: it could not be done). Any task. */
void ul_core_press_done(const char *channel_id, bool ok);

/* ---------- boot ---------- */

/* NVS, event loop, board, drivers, outputs, then services. Called by app_main only. */
esp_err_t ul_core_boot(void);

/* ---------- outputs (services) ---------- */

int ul_core_output_count(void);
int ul_core_find_output(const char *key);
bool ul_core_output_def(int idx, ul_output_def_t *out);
bool ul_core_output_state(int idx, ul_state_msg_t *out);

/* Copy of the current mapping (caps filled) for info. */
void ul_core_config(ul_config_t *out);
int ul_core_config_rev(void);
/* Why the last config or the board was rejected, or NULL. Valid until the next call into ul_core. */
const char *ul_core_config_error(void);

/*
 * Apply a set (live = false) or live (live = true) message to an output.
 * ESP_OK applied, ESP_ERR_INVALID_STATE stale seq, ESP_ERR_NOT_FOUND no such output.
 */
esp_err_t ul_core_apply(const char *key, const ul_state_msg_t *msg, bool live);

/* Apply a config message; an empty payload restores the default mapping. */
esp_err_t ul_core_apply_config(const char *json, size_t len);

/* Solenoid pulse, clamped to the output's max_on_ms. */
esp_err_t ul_core_pulse(const char *key, uint32_t ms);

/* End a solenoid's running pulse now (no-op if it is closed). Not retained, unlike set. */
esp_err_t ul_core_close(const char *key);

/*
 * cycle: the appliance is really at `at` ("off" or a mode). Taken as both the
 * position and the target, without pressing. ESP_ERR_INVALID_STATE while a press
 * runs, ESP_ERR_INVALID_ARG for a mode the output doesn't have.
 */
esp_err_t ul_core_sync(const char *key, const char *at, const char *src);

/* Blink one output (key) or all of them (NULL) for ms, then restore. */
esp_err_t ul_core_identify(const char *key, uint32_t ms);

/* Erase saved state and config (factory reset helper). */
esp_err_t ul_core_forget(void);

#ifdef __cplusplus
}
#endif

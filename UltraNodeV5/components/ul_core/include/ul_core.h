#pragma once
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

void ul_core_wifi_start(void);
bool ul_core_wait_for_ip(TickType_t timeout);
bool ul_core_is_connected(void);
void ul_core_wifi_stop(void); // Call before reinitializing or shutting down networking
void ul_core_sntp_start(void);
void ul_core_schedule_daily_reboot(void);
const char *ul_core_get_node_id(void);

typedef void (*ul_core_conn_cb_t)(bool connected, void *ctx);
void ul_core_register_connectivity_cb(ul_core_conn_cb_t cb, void *ctx);

#ifdef __cplusplus
}
#endif

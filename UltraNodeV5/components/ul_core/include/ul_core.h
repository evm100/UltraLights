#pragma once
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void ul_core_wifi_start(void);
bool ul_core_wait_for_ip(TickType_t timeout);
bool ul_core_is_connected(void);
void ul_core_wifi_stop(void); // Call before reinitializing or shutting down networking
void ul_core_sntp_start(void);
const char *ul_core_get_node_id(void);
bool ul_core_is_sntp_resync_active(void);
uint32_t ul_core_get_sntp_retry_attempts(void);
uint64_t ul_core_get_sntp_first_failure_us(void);
uint64_t ul_core_get_sntp_last_failure_us(void);

typedef void (*ul_core_conn_cb_t)(bool connected, void *ctx);
void ul_core_register_connectivity_cb(ul_core_conn_cb_t cb, void *ctx);

typedef void (*ul_core_time_sync_cb_t)(void *ctx);
void ul_core_register_time_sync_cb(ul_core_time_sync_cb_t cb, void *ctx);

void ul_core_wifi_restart(void);

#ifdef __cplusplus
}
#endif

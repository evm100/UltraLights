#pragma once
#include "esp_err.h"
#include <stdbool.h>
#include "freertos/FreeRTOS.h"

#ifdef __cplusplus
extern "C" {
#endif

void ul_core_wifi_start(void);
bool ul_core_wait_for_ip(TickType_t timeout);
bool ul_core_is_connected(void);
void ul_core_sntp_start(void);
const char* ul_core_get_node_id(void);

#ifdef __cplusplus
}
#endif

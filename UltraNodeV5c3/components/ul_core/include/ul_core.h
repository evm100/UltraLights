#pragma once
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

void ul_core_wifi_start_blocking(void);
void ul_core_sntp_start(void);
const char* ul_core_get_node_id(void);

#ifdef __cplusplus
}
#endif

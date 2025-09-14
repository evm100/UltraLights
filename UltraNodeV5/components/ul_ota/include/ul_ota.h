#pragma once
#include "sdkconfig.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#if CONFIG_UL_OTA_ENABLED
void ul_ota_start(void);
void ul_ota_stop(void);
// Triggered via MQTT: ul/<node_id>/cmd/ota/check
void ul_ota_check_now(bool force);
#else
static inline void ul_ota_start(void) {}
static inline void ul_ota_stop(void) {}
static inline void ul_ota_check_now(bool force) {}
#endif

#ifdef __cplusplus
}
#endif

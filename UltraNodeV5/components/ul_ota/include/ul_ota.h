#pragma once
#include <stdbool.h>

/* OTA functions are always available; CONFIG_UL_OTA_AUTO_CHECK controls
 * only the periodic background task.
 */
#ifdef __cplusplus
extern "C" {
#endif

void ul_ota_start(void);
void ul_ota_stop(void);
// Triggered via MQTT: ul/<node_id>/cmd/ota/check
void ul_ota_check_now(bool force);

#ifdef __cplusplus
}
#endif

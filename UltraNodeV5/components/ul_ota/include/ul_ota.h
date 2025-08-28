#pragma once
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

void ul_ota_start(void);
// Triggered via MQTT: ul/<node_id>/cmd/ota/check
void ul_ota_check_now(bool force);

#ifdef __cplusplus
}
#endif

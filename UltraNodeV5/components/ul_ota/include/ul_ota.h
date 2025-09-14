#pragma once
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Perform a firmware update immediately.
// Triggered via MQTT: ul/<node_id>/cmd/ota/check
void ul_ota_check_now(bool force);

#ifdef __cplusplus
}
#endif

#pragma once
#include "sdkconfig.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Triggered via MQTT: ul/<node_id>/cmd/ota/check
// manifest_url_override: if non-NULL and non-empty, use this URL instead of
// the compiled-in CONFIG_UL_OTA_MANIFEST_URL.
void ul_ota_check_now(bool force, const char *manifest_url_override);

#ifdef __cplusplus
}
#endif


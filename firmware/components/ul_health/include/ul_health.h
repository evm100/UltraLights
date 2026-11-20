/*
 * ul_health: device metrics for the health topic (contracts/schemas/health).
 * ul_mqtt publishes them every 60 s and adds its own reconnect count.
 */
#pragma once

#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Fill every field except mqtt_reconnects. Strings point at static storage. */
void ul_health_collect(ul_health_t *h);

#ifdef __cplusplus
}
#endif

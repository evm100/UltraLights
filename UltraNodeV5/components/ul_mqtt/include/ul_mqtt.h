#pragma once
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void ul_mqtt_start(void);
void ul_mqtt_stop(void);
void ul_mqtt_publish_status(void);
void ul_mqtt_publish_status_now(void);
// Publish a motion event for a specific sensor. The event is published on
// topic "ul/<node>/evt/<sensor>/motion" with the given state string.
void ul_mqtt_publish_motion(const char *sensor, const char *state);
void ul_mqtt_publish_ota_event(const char *status, const char *detail);
bool ul_mqtt_is_ready(void);
bool ul_mqtt_is_connected(void);

// Execute a command locally without publishing over MQTT. The path should match
// the suffix of a normal command topic (e.g. "ws/set").
void ul_mqtt_run_local(const char *path, const char *json);

#ifdef __cplusplus
}
#endif

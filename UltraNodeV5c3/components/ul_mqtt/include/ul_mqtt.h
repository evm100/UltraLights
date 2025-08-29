#pragma once
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void ul_mqtt_start(void);
void ul_mqtt_publish_status(void);
void ul_mqtt_publish_status_now(void);
void ul_mqtt_publish_motion(const char* sid, const char* state);
bool ul_mqtt_is_ready(void);

#ifdef __cplusplus
}
#endif

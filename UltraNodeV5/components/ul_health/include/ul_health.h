#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*ul_health_recovery_cb_t)(void *ctx);

typedef struct {
  ul_health_recovery_cb_t request_wifi_recovery;
  ul_health_recovery_cb_t request_mqtt_recovery;
  void *ctx;
} ul_health_config_t;

void ul_health_start(const ul_health_config_t *config);
void ul_health_notify_connectivity(bool connected);
void ul_health_notify_mqtt(bool connected);
void ul_health_notify_time_sync(void);
void ul_health_notify_rgb_engine_ok(void);
void ul_health_notify_rgb_engine_failure(void);

#ifdef __cplusplus
}
#endif

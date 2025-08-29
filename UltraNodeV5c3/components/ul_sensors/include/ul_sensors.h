#pragma once
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

void ul_sensors_start(void);
void ul_sensors_set_cooldown(int seconds); // via MQTT 10..3600

typedef struct {
    int cooldown_s;
    bool pir_enabled;
    bool ultra_enabled;
    bool pir_active;
    bool ultra_active;
    int near_threshold_mm;
} ul_sensor_status_t;

void ul_sensors_get_status(ul_sensor_status_t* out);

#ifdef __cplusplus
}
#endif

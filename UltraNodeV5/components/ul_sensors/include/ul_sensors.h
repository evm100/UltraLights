#pragma once
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

void ul_sensors_start(void);
void ul_sensors_set_cooldown(int seconds); // legacy: sets both motion times
void ul_sensors_set_pir_motion_time(int seconds);
void ul_sensors_set_sonic_motion_time(int seconds);
void ul_sensors_set_sonic_threshold_mm(int mm);
void ul_sensors_set_motion_on_channel(int ch);

typedef struct {
    int pir_motion_time_s;
    int sonic_motion_time_s;
    int sonic_threshold_mm;
    int motion_on_channel;
    bool pir_enabled;
    bool ultra_enabled;
    bool pir_active;
    bool ultra_active;
} ul_sensor_status_t;

void ul_sensors_get_status(ul_sensor_status_t* out);

#ifdef __cplusplus
}
#endif

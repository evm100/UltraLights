#pragma once
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

void ul_sensors_start(void);
void ul_sensors_stop(void);
void ul_sensors_set_cooldown(int seconds); // legacy: sets both motion times
void ul_sensors_set_pir_motion_time(int seconds);
void ul_sensors_set_sonic_motion_time(int seconds);
void ul_sensors_set_sonic_threshold_mm(int mm);
void ul_sensors_set_motion_on_channel(int ch);

typedef enum {
    UL_MOTION_NONE = 0,
    UL_MOTION_DETECTED = 1, // PIR motion
    UL_MOTION_NEAR = 2,     // Ultrasonic within threshold
} ul_motion_state_t;

// Set MQTT commands to execute when entering a motion state. Pass NULL for any
// command to leave it unchanged. Commands should be JSON payloads for the
// corresponding "ws/set" or "white/set" paths.
void ul_sensors_set_motion_command(ul_motion_state_t state, const char* ws_cmd, const char* white_cmd);

typedef struct {
    int pir_motion_time_s;
    int sonic_motion_time_s;
    int sonic_threshold_mm;
    int motion_on_channel;
    bool pir_enabled;
    bool ultra_enabled;
    bool pir_active;
    bool ultra_active;
    ul_motion_state_t motion_state;
} ul_sensor_status_t;

void ul_sensors_get_status(ul_sensor_status_t* out);

#ifdef __cplusplus
}
#endif

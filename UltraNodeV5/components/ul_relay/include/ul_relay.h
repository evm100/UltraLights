#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct cJSON cJSON;

#ifdef __cplusplus
extern "C" {
#endif

bool ul_relay_start(void);
void ul_relay_stop(void);

// Parse and apply a JSON payload for relay/set
bool ul_relay_apply_json(cJSON *root, int *out_channel, bool *out_desired);

bool ul_relay_set_state(int channel, bool on);
int ul_relay_get_channel_count(void);

typedef struct {
  bool enabled;
  bool state;
  bool active_high;
  int gpio;
  uint32_t min_interval_ms;
  uint64_t last_change_us;
} ul_relay_status_t;

bool ul_relay_get_status(int channel, ul_relay_status_t *out);

#ifdef __cplusplus
}
#endif

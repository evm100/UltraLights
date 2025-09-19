#pragma once

#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Initializes the persistence pipeline. Must be called after NVS is ready.
void ul_state_init(void);

// Records the most recent MQTT command for the given target. The payload is
// copied immediately so callers may release their buffers as soon as the call
// returns. The payload length should exclude the terminating null byte.
void ul_state_record_ws(int strip, const char *payload, size_t len);
void ul_state_record_rgb(int strip, const char *payload, size_t len);
void ul_state_record_white(int channel, const char *payload, size_t len);

#ifdef __cplusplus
}
#endif

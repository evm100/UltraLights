#pragma once

#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#define UL_STATE_MAX_JSON_LEN 1024

#ifdef __cplusplus
extern "C" {
#endif

// Initializes the persistence pipeline. Must be called after NVS is ready.
// Returns ESP_OK on success or an error code if persistence could not be
// initialized (e.g. due to memory pressure).
esp_err_t ul_state_init(void);

// Records the most recent MQTT command for the given target. The payload is
// copied immediately so callers may release their buffers as soon as the call
// returns. Persistence is deferred for several seconds after the most recent
// update so rapid command bursts never block the lighting path. The payload
// length should exclude the terminating null byte.

void ul_state_record_ws(int strip, const char *payload, size_t len);
void ul_state_record_rgb(int strip, const char *payload, size_t len);
void ul_state_record_white(int channel, const char *payload, size_t len);

// Copies the most recent persisted JSON payload for the requested target into
// the caller-provided buffer. The copy includes the terminating null byte. The
// buffer is cleared and false is returned if no payload has been recorded or
// the buffer is too small.
bool ul_state_copy_ws(int strip, char *buffer, size_t buffer_len);
bool ul_state_copy_rgb(int strip, char *buffer, size_t buffer_len);
bool ul_state_copy_white(int channel, char *buffer, size_t buffer_len);

#ifdef __cplusplus
}
#endif

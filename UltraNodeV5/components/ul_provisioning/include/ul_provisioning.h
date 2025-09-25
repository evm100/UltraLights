#pragma once

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  char ap_ssid[33];
  char ap_password[65];
  uint8_t channel;
  uint32_t inactivity_timeout_ms;
} ul_provisioning_config_t;

void ul_provisioning_make_default_config(ul_provisioning_config_t *cfg);

esp_err_t ul_provisioning_start(const ul_provisioning_config_t *cfg);

bool ul_provisioning_wait_for_completion(TickType_t ticks_to_wait, char *ip_buffer,
                                         size_t ip_buffer_len);

void ul_provisioning_stop(void);

const char *ul_provisioning_get_state_string(void);

#ifdef __cplusplus
}
#endif

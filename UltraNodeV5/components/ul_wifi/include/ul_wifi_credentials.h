#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  char ssid[33];
  char password[65];
  char user[65];
  char user_password[129];
} ul_wifi_credentials_t;

bool ul_wifi_credentials_load(ul_wifi_credentials_t *out);
esp_err_t ul_wifi_credentials_save(const ul_wifi_credentials_t *creds);
esp_err_t ul_wifi_credentials_clear(void);

#ifdef __cplusplus
}
#endif

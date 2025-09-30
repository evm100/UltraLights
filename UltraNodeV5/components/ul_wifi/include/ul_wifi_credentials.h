#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#if defined(__has_include)
#if __has_include("sdkconfig.h")
#include "sdkconfig.h"
#endif
#endif

#ifndef CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN
#define CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN 3072
#endif

#ifndef CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN
#define CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN 2048
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  char ssid[33];
  char password[65];
  char user[65];
  char user_password[129];
  char wifi_username[65];
  char wifi_user_password[129];
  uint8_t mqtt_client_cert[CONFIG_UL_MQTT_CLIENT_CERT_MAX_LEN];
  size_t mqtt_client_cert_len;
  uint8_t mqtt_client_key[CONFIG_UL_MQTT_CLIENT_KEY_MAX_LEN];
  size_t mqtt_client_key_len;
} ul_wifi_credentials_t;

bool ul_wifi_credentials_load(ul_wifi_credentials_t *out);
esp_err_t ul_wifi_credentials_save(const ul_wifi_credentials_t *creds);
esp_err_t ul_wifi_credentials_clear(void);

#ifdef __cplusplus
}
#endif

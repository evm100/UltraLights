#pragma once
#include "esp_err.h"
#include "esp_event.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  int dummy;
} wifi_init_config_t;

#define WIFI_INIT_CONFIG_DEFAULT() (wifi_init_config_t){0}

#define WIFI_MODE_STA 0
#define WIFI_IF_STA 0
#define WIFI_AUTH_WPA2_PSK 1

typedef struct {
  struct {
    uint8_t ssid[32];
    uint8_t password[64];
    struct {
      int authmode;
    } threshold;
  } sta;
} wifi_config_t;

esp_err_t esp_wifi_init(const wifi_init_config_t *cfg);
esp_err_t esp_wifi_set_mode(int mode);
esp_err_t esp_wifi_set_config(int interface, wifi_config_t *config);
esp_err_t esp_wifi_start(void);
esp_err_t esp_wifi_stop(void);
esp_err_t esp_wifi_deinit(void);
esp_err_t esp_wifi_connect(void);

#define WIFI_EVENT ((esp_event_base_t)"WIFI_EVENT")
#define IP_EVENT ((esp_event_base_t)"IP_EVENT")
#define WIFI_EVENT_STA_START 0
#define WIFI_EVENT_STA_DISCONNECTED 1
#define IP_EVENT_STA_GOT_IP 2

#ifdef __cplusplus
}
#endif

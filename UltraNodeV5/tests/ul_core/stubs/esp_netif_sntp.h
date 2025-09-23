#pragma once
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  const char *server_from_dhcp;
} esp_sntp_config_t;

#define ESP_NETIF_SNTP_DEFAULT_CONFIG(server) \
  (esp_sntp_config_t){ .server_from_dhcp = (server) }

esp_err_t esp_netif_sntp_init(const esp_sntp_config_t *config);
esp_err_t esp_netif_sntp_start(void);

#ifdef __cplusplus
}
#endif

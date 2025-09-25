#pragma once

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct dns_server_handle_t dns_server_handle_t;

esp_err_t ul_dns_server_start(uint32_t ip_addr, dns_server_handle_t **out_handle);
void ul_dns_server_stop(dns_server_handle_t *handle);

#ifdef __cplusplus
}
#endif

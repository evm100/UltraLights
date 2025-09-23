#pragma once
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
  struct {
    struct {
      uint32_t addr;
    } ip;
  } ip_info;
} ip_event_got_ip_t;

static inline void *esp_netif_create_default_wifi_sta(void) { return (void *)1; }

#define IPSTR "%d.%d.%d.%d"
#define IP2STR(ip) 0, 0, 0, 0

#ifdef __cplusplus
}
#endif

/*
 * ul_wifi: station connection with backoff, the optional DNS override and
 * time sync. Credentials and DNS live in NVS (namespace "ul_wifi"). On first
 * boot they are seeded from the ul_board partition when it carries them.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_netif.h"

#ifdef __cplusplus
extern "C" {
#endif

bool ul_wifi_has_credentials(void);

/* Save credentials (psk may be "", dns may be NULL or "" for the router's DNS). Takes effect on restart. */
esp_err_t ul_wifi_save(const char *ssid, const char *psk, const char *dns);

/* Forget Wi-Fi and DNS; identity is kept. */
esp_err_t ul_wifi_forget(void);

/* Start the setup access point next to the station (APSTA). Returns its netif. */
esp_netif_t *ul_wifi_start_ap(const char *ssid);
void ul_wifi_stop_ap(void);

bool ul_wifi_connected(void);
int ul_wifi_rssi(void);              /* 0 when not connected */
int ul_wifi_channel(void);           /* 0 when not connected */
void ul_wifi_ip(char out[16]);       /* "" when not connected */
void ul_wifi_dns(char out[16]);      /* DNS server in use, "" when none */
uint32_t ul_wifi_reconnects(void);
bool ul_wifi_time_synced(void);

#ifdef __cplusplus
}
#endif

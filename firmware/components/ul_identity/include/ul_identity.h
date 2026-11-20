/*
 * ul_identity: the node's TLS identity. The EC P-256 key is generated on the
 * chip at first boot and never leaves it. With a one-time token from the
 * ul_board partition the node enrolls over HTTPS (contracts/README.md
 * "Enrollment") and keeps the signed certificate in NVS (namespace "ul_id").
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

bool ul_identity_ready(void);
const char *ul_identity_device_id(void);   /* from the descriptor, NULL without one */
const char *ul_identity_cert_chain(void);  /* PEM leaf + intermediate, NUL-terminated */
const char *ul_identity_key(void);         /* PEM private key, NUL-terminated */
const char *ul_identity_mqtt_host(void);   /* from the enrollment response */
uint16_t ul_identity_mqtt_port(void);
const char *ul_identity_server_ca(void);   /* provision.server_ca, or NULL for the public bundle */

/* Erase key and certificate (factory reset). The node needs a new token afterwards. */
esp_err_t ul_identity_forget(void);

#ifdef __cplusplus
}
#endif

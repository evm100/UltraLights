/* Key and CSR helpers (mbedTLS 4 / PSA). Split out so a QEMU test can run them. */
#pragma once

#include "esp_err.h"

/* Generate an EC P-256 key on the chip; *pem is a malloc'd PEM private key. */
esp_err_t ul_id_generate_key(char **pem);

/* A PEM CSR (malloc'd) signed by key_pem with subject CN=device_id, or NULL. */
char *ul_id_make_csr(const char *key_pem, const char *device_id);

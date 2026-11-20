/*
 * ul_provision: the setup access point and page (Wi-Fi, password, DNS) and the
 * reset button from the board descriptor. Opens by itself when there are no
 * credentials, when the button is held, or when saved credentials fail for
 * 3 minutes.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t ul_provision_open_portal(void);
bool ul_provision_portal_open(void);

#ifdef __cplusplus
}
#endif

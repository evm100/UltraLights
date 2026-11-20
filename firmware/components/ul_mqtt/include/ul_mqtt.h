/*
 * ul_mqtt: the MQTT client and every contract topic (contracts/README.md).
 * Connects with mutual TLS once ul_identity is ready, follows the connection
 * sequence, feeds config/set/live into ul_core, publishes info/status/state/
 * evt/health, and answers commands.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * A component that handles a command op (e.g. ul_ota for "ota"). It returns
 * ESP_OK once it has taken the command and then publishes the result itself
 * with ul_mqtt_cmd_result(); any other return is published as a failure.
 */
typedef esp_err_t (*ul_mqtt_cmd_handler_t)(const ul_cmd_t *cmd);
void ul_mqtt_register_cmd(ul_op_t op, ul_mqtt_cmd_handler_t handler);

/* progress: -1 for none (the final result), 0..100 while running. error/detail may be NULL. */
esp_err_t ul_mqtt_cmd_result(const char *cmd_id, bool ok, const char *error, const char *detail, int progress);

esp_err_t ul_mqtt_publish_evt(const ul_evt_t *e);
bool ul_mqtt_connected(void);

#ifdef __cplusplus
}
#endif

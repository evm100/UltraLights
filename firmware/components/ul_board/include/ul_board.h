/*
 * ul_board: reads the board descriptor and provisioning data from the
 * ul_board partition (contracts/board-partition.md) and checks it against the
 * chip it runs on and the pin rules in contracts/chips.json.
 */
#pragma once

#include "esp_err.h"
#include "ul_board_rules.h"
#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The chip this image was built for (from the per-chip source file). */
ul_chip_t ul_board_running_chip(void);

/* Read and check the partition. Call once at boot. Never fails hard: problems are reported by the getters. */
esp_err_t ul_board_load(void);

/* The descriptor if it passed every check, else NULL (no outputs are driven). */
const ul_board_desc_t *ul_board_get(void);

/* The descriptor as read, even if rejected (for its name in info), else NULL. */
const ul_board_desc_t *ul_board_raw(void);

/* Provisioning data, or NULL when the partition is missing or unreadable. */
const ul_provision_t *ul_board_provision(void);

/* CRC-32 of the payload (board_crc in info), 0 when there is none. */
uint32_t ul_board_crc(void);

/* Why the board was rejected or missing ("erased", "pin_conflict:0", ...), or NULL. */
const char *ul_board_error(void);

/* Pin warnings from the last load, for the log and the setup page. */
const ul_pin_report_t *ul_board_pin_report(void);

#ifdef __cplusplus
}
#endif

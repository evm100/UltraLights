/*
 * Pure-C parts of ul_board: the ul_board blob header, CRC-32 and the pin
 * rules from contracts/chips.json. Host-testable; no ESP-IDF headers.
 */
#pragma once

#include "ul_types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define UL_BOARD_MAGIC        "ULBD"
#define UL_BOARD_FORMAT       1
#define UL_BOARD_HEADER_SIZE  16
#define UL_GPIO_LIMIT         31   /* gpio 0..30 per schema */

typedef struct {
    const char *name;
    int gpio_max;
    int boot_button;
    int max_pwm, max_pwm_freqs, max_argb;
    const char *unusable[UL_GPIO_LIMIT];  /* reason, NULL = usable */
    const char *caution[UL_GPIO_LIMIT];   /* reason, NULL = fine */
} ul_chip_rules_t;

extern const ul_chip_rules_t UL_CHIP_RULES[UL_CHIP_COUNT];

/* IEEE 802.3 CRC-32, identical to zlib.crc32(data). */
uint32_t ul_crc32(const void *data, size_t len);

/*
 * Check the header of a ul_board partition image. On success *payload points
 * into blob, *len is the payload length and *crc its CRC. Returns 0, or -1
 * with a reason ("erased", "bad magic", ...) in err.
 */
int ul_board_blob_parse(const uint8_t *blob, size_t size, const char **payload, size_t *len,
                        uint32_t *crc, char *err, size_t errlen);

#define UL_PIN_REPORT_MAX 48
#define UL_PIN_CODE_SZ    32

/* Results use the same codes as contracts/tools/pins.py: "<code>:<detail>", sorted. */
typedef struct {
    int n_err, n_warn;
    char err[UL_PIN_REPORT_MAX][UL_PIN_CODE_SZ];
    char warn[UL_PIN_REPORT_MAX][UL_PIN_CODE_SZ];
} ul_pin_report_t;

void ul_board_check_pins(const ul_board_desc_t *b, ul_pin_report_t *r);

#ifdef __cplusplus
}
#endif

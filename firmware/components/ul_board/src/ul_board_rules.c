/* Blob header, CRC-32 and pin rules. Mirrors contracts/tools/pins.py. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ul_board_rules.h"

uint32_t ul_crc32(const void *data, size_t len)
{
    static uint32_t table[256];
    static int ready;
    if (!ready) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) {
                c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            }
            table[i] = c;
        }
        ready = 1;
    }
    const uint8_t *p = data;
    uint32_t crc = 0xFFFFFFFFu;
    while (len--) {
        crc = table[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }

int ul_board_blob_parse(const uint8_t *blob, size_t size, const char **payload, size_t *len,
                        uint32_t *crc, char *err, size_t errlen)
{
    if (size < UL_BOARD_HEADER_SIZE) {
        snprintf(err, errlen, "partition too small");
        return -1;
    }
    if (le32(blob) == 0xFFFFFFFFu) {
        snprintf(err, errlen, "erased");
        return -1;
    }
    if (memcmp(blob, UL_BOARD_MAGIC, 4) != 0) {
        snprintf(err, errlen, "bad magic");
        return -1;
    }
    if (le16(blob + 4) != UL_BOARD_FORMAT) {
        snprintf(err, errlen, "unsupported format %u", le16(blob + 4));
        return -1;
    }
    uint32_t n = le32(blob + 8);
    if (n == 0 || n > size - UL_BOARD_HEADER_SIZE) {
        snprintf(err, errlen, "bad length %u", (unsigned)n);
        return -1;
    }
    uint32_t want = le32(blob + 12);
    uint32_t got = ul_crc32(blob + UL_BOARD_HEADER_SIZE, n);
    if (got != want) {
        snprintf(err, errlen, "crc mismatch");
        return -1;
    }
    *payload = (const char *)blob + UL_BOARD_HEADER_SIZE;
    *len = n;
    *crc = got;
    return 0;
}

/* ---------- pin rules ---------- */

static void add(char list[][UL_PIN_CODE_SZ], int *n, const char *code, const char *detail)
{
    char s[UL_PIN_CODE_SZ];
    snprintf(s, sizeof(s), "%s:%s", code, detail);
    for (int i = 0; i < *n; i++) {
        if (strcmp(list[i], s) == 0) {
            return;
        }
    }
    if (*n < UL_PIN_REPORT_MAX) {
        strcpy(list[(*n)++], s);
    }
}

static void addi(char list[][UL_PIN_CODE_SZ], int *n, const char *code, int v)
{
    char d[12];
    snprintf(d, sizeof(d), "%d", v);
    add(list, n, code, d);
}

static int cmp(const void *a, const void *b) { return strcmp(a, b); }

void ul_board_check_pins(const ul_board_desc_t *b, ul_pin_report_t *r)
{
    const ul_chip_rules_t *chip = &UL_CHIP_RULES[b->chip];
    bool used[UL_GPIO_LIMIT] = {0};
    memset(r, 0, sizeof(*r));

#define CLAIM(gpio, is_reset)                                                             \
    do {                                                                                  \
        int g_ = (gpio);                                                                  \
        if (g_ > chip->gpio_max) {                                                        \
            addi(r->err, &r->n_err, "no_such_pin", g_);                                   \
        } else if (chip->unusable[g_]) {                                                  \
            addi(r->err, &r->n_err, "unusable_pin", g_);                                  \
        } else if (chip->caution[g_] && !((is_reset) && g_ == chip->boot_button)) {       \
            addi(r->warn, &r->n_warn, "caution_pin", g_);                                 \
        }                                                                                 \
        if (g_ >= 0 && g_ < UL_GPIO_LIMIT) {                                              \
            if (used[g_]) {                                                               \
                addi(r->err, &r->n_err, "pin_conflict", g_);                              \
            }                                                                             \
            used[g_] = true;                                                              \
        }                                                                                 \
    } while (0)

    int pwm = 0, argb = 0, n_freqs = 0;
    uint32_t freqs[UL_MAX_CHANNELS];
    for (int i = 0; i < b->n_channels; i++) {
        const ul_channel_desc_t *ch = &b->ch[i];
        for (int k = 0; k < i; k++) {
            if (strcmp(b->ch[k].id, ch->id) == 0) {
                add(r->err, &r->n_err, "duplicate_id", ch->id);
            }
        }
        CLAIM(ch->gpio, false);
        if (ch->type == UL_CH_PWM) {
            pwm++;
            bool seen = false;
            for (int k = 0; k < n_freqs; k++) {
                seen |= freqs[k] == ch->hz;
            }
            if (!seen) {
                freqs[n_freqs++] = ch->hz;
            }
        } else if (ch->type == UL_CH_ARGB) {
            argb++;
        }
    }
    if (b->has_reset) {
        CLAIM(b->reset_gpio, true);
    } else {
        add(r->warn, &r->n_warn, "no_reset_button", "");
    }
    if (b->has_status) {
        CLAIM(b->status_gpio, false);
    }
#undef CLAIM
    if (pwm > chip->max_pwm) {
        addi(r->err, &r->n_err, "too_many_pwm", pwm);
    }
    if (n_freqs > chip->max_pwm_freqs) {
        addi(r->err, &r->n_err, "too_many_pwm_freqs", n_freqs);
    }
    if (argb > chip->max_argb) {
        addi(r->err, &r->n_err, "too_many_argb", argb);
    }
    qsort(r->err, (size_t)r->n_err, UL_PIN_CODE_SZ, cmp);
    qsort(r->warn, (size_t)r->n_warn, UL_PIN_CODE_SZ, cmp);
}

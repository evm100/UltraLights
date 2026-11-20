/* RMT layout of the chip this image is built for (per-chip source file). */
#pragma once

typedef struct {
    int tx_channels;      /* channels that can transmit, numbered from 0 */
    int mem_blocks;       /* 48-symbol blocks; a TX channel may borrow the ones after it */
} ul_argb_chip_t;

extern const ul_argb_chip_t UL_ARGB_CHIP;

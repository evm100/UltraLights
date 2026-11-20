/* ESP32-C6 RMT: TX on channels 0-1, RX on 2-3, four 48-symbol blocks, no DMA (TRM 37.3.2.2). */
#include "ul_argb_chip.h"

const ul_argb_chip_t UL_ARGB_CHIP = {.tx_channels = 2, .mem_blocks = 4};

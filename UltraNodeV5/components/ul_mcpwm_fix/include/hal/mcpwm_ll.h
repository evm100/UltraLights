#pragma once

// Rename the original inline helper so we can provide a patched version below.
// The original definition will be emitted with the `_original` suffix and kept
// available for reference if ever needed.
#define mcpwm_ll_gen_trigger_noncontinue_force_action \
    mcpwm_ll_gen_trigger_noncontinue_force_action_original
#include_next "hal/mcpwm_ll.h"
#undef mcpwm_ll_gen_trigger_noncontinue_force_action

/**
 * @brief Trigger a non-continuous forced action for the given MCPWM generator.
 *
 * The upstream ESP-IDF implementation toggles the trigger bit by applying a
 * bitwise NOT to the backing field. GCC 12 miscompiles that pattern when
 * building with the ESP-IDF toolchain, which results in an internal compiler
 * error during the bootloader build. Using an XOR toggle avoids the ICE while
 * keeping the semantics identical because the fields are single-bit values.
 */
static inline void mcpwm_ll_gen_trigger_noncontinue_force_action(mcpwm_dev_t *mcpwm,
                                                                 int operator_id,
                                                                 int generator_id)
{
    if (generator_id == 0) {
        mcpwm->operators[operator_id].gen_force.gen_a_nciforce ^= 1;
    } else {
        mcpwm->operators[operator_id].gen_force.gen_b_nciforce ^= 1;
    }
}

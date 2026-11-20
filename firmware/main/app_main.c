/*
 * Boot order only. Every feature lives in a ul_* component and registers
 * itself with ul_core; see docs/blueprint.html#firmware.
 */
#include "ul_core.h"

void app_main(void)
{
    ul_core_boot();
}

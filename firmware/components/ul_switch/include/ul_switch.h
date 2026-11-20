/*
 * ul_switch: GPIO driver for every `switch` channel (relay and solenoid
 * coils). Registers with ul_core at the driver stage. Solenoid time limits
 * are enforced by ul_core (max_on_ms); a hardware timeout on the PCB is the
 * second line of defence.
 */
#pragma once

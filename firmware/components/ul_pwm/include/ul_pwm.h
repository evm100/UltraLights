/*
 * ul_pwm: LEDC driver for every `pwm` channel in the board descriptor.
 * Registers with ul_core at the driver stage; nothing to call directly.
 * Fades run in hardware: one linear LEDC fade for short changes (live
 * sliders), and a curved fade through several points for long ones
 * (hardware multi-fade on the C6, chained hardware fades on the C3).
 */
#pragma once

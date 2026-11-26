/*
 * ul_servo: LEDC driver for every `servo` channel. A servo presses an
 * appliance's own mode button; ul_core decides how many presses a cycle
 * output needs and calls ops->press once per press. Registers with ul_core at
 * the driver stage.
 *
 * LEDC is shared with ul_pwm: ul_pwm takes channels and timers from 0 up,
 * ul_servo takes channels from the top down and the last timer (50 Hz, shared
 * by all servos). The board rules (contracts/tools/pins.py) count servos as
 * PWM channels and 50 Hz as one PWM frequency, so the two never meet.
 */
#pragma once

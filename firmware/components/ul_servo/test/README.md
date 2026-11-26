# ul_servo tests

Press counting and planning are host-tested in `ul_core/test` (`cycle_*`), and
the servo channel's descriptor in `ul_contract/test`. On target, with a scope or
the servo itself:

- At boot the pin stays low, then the arm goes to `rest_deg` and the signal
  stops after `release_ms` (no hum).
- One press: 50 Hz pulses for `press_deg` for `hold_ms`, then `rest_deg` for
  `release_ms`, then no signal. 0 degrees is 500 us, 180 degrees 2500 us
  (`CONFIG_UL_SERVO_MIN_US` / `MAX_US`).
- With five PWM channels and a servo on one board, the lights still fade
  (the servo uses the last LEDC channel and timer).

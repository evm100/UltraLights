# ul_pwm tests

No host tests: this component is only LEDC calls. The duty math it relies on
(curve, CCT mix, min_duty, max_level) is tested in `ul_core/test`.

On-target checks still to write (need a board and a scope or logic analyser):
- each channel's frequency and duty match the descriptor and the rendered value;
- a 60 ms live fade and a 2 s curved fade end exactly on the target duty, on both chips;
- `invert` flips the output.

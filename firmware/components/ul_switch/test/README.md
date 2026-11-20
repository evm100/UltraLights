# ul_switch tests

Pulse limits and relay rendering are tested in `ul_core/test`. On target:
the pin must stay inactive through boot (scope on a relay channel during reset),
and `active_low` must invert it.

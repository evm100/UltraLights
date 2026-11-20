# ul_ota tests

On-target checks still to write (dev server serving a package's app image):
- progress results at 10 % steps, then ok, restart, and `health.ota` = valid after the broker connects;
- wrong `sha256` or `version` in the command: aborted with that error, the node keeps running;
- an image that can't reach the broker rolls back after a reset.

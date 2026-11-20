# ul_mqtt tests

Payloads are host-tested in `ul_contract/test`. On-target checks still to write,
against the dev broker with `mosquitto_sub -v -t 'ul/v1/#'`:
- connection sequence: status online, info, health, one state per output, all retained as listed;
- killing the node's power publishes the will `{"online":false}`;
- a live stream at 20 Hz produces at most 5 state messages per second and always the final one;
- a new config with renamed outputs gets the retained sets for the new keys (resubscribe);
- each cmd op answers with one cmd_result carrying the same id; a cmd older than 30 s is `stale`.

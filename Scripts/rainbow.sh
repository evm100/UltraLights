#!/bin/bash

mosquitto_pub -t "ul/node01/cmd/ws/set/0" -m "{

\"brightness\": 255,
\"speed\": 2,
\"strip\": 0,
\"effect\": \"rainbow\",
\"params\": [200]
}" -r

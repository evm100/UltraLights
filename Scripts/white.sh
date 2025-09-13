#!/bin/bash

mosquitto_pub -t "ul/living-room-1/cmd/white/set" -m "{
\"channel\": 0,
\"brightness\": 255,
\"effect\": \"breath\",
\"params\": [100]
}" -r

#!/usr/bin/env bash
# Provision node firmware and append output to secrets.txt

# Prompt for ESP32 type (directory name under ../../Configs/)
read -rp "Enter ESP32 type (e.g., esp32, esp32c3, esp32s3): " esp32
if [[ -z "$esp32" ]]; then
  echo "Error: ESP32 type is required" >&2
  exit 1
fi

# Prompt for node ID
read -rp "Enter node ID: " nodeid
if [[ -z "$nodeid" ]]; then
  echo "Error: node ID is required" >&2
  exit 1
fi

# Construct the config path
config_path="../../Configs/$esp32/sdkconfig.$nodeid"

# Check if config file exists
if [[ ! -f "$config_path" ]]; then
  echo "Error: Config file $config_path not found" >&2
  exit 1
fi

# Run the Python script with the provided values
.venv/bin/python3 scripts/provision_node_firmware.py "$nodeid" \
  --config "$config_path" >> "../../Configs/secrets.txt" \
  --allow-reprovision

# Confirm completion
if [[ $? -eq 0 ]]; then
  echo "Provisioning completed for node $nodeid ($esp32) and appended to secrets.txt"
else
  echo "Provisioning failed for node $nodeid ($esp32)" >&2
fi

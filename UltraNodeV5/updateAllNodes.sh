#!/usr/bin/env bash
# UltraLights multi-target bulk flasher + versioned artifact rotator

set -euo pipefail

# --- CONFIG ---
CONFIG_ROOT="../../Configs"
FIRMWARE_DIR="/srv/firmware/UltraLights"

# FLASH command:
# - If you have an alias/function `flash` in ~/.bashrc, we'll source it.
# - Otherwise, set FLASH_CMD env var before running this script, e.g.:
#     FLASH_CMD="idf.py -p /dev/ttyUSB0 flash" ./bulk_flash.sh
# - Default fallback is plain "idf.py build".
DEFAULT_FLASH_CMD="idf.py build"

# --- SHELL SETUP ---
shopt -s nullglob
shopt -s expand_aliases
# Source aliases/functions if present (won't fail if missing)
[[ -f ~/.bashrc ]] && source ~/.bashrc || true

# Resolve FLASH_CMD
FLASH_CMD="${FLASH_CMD:-$DEFAULT_FLASH_CMD}"

echo "Using flash command: $FLASH_CMD"
echo

# --- HELPERS ---
safe_rm_build() {
  # Only remove local ./build, never an absolute path.
  local target="./build"
  if [[ -d "$target" ]]; then
    echo "Removing $target ..."
    rm -rf "$target"
  else
    echo "No build directory to remove."
  fi
}

archive_and_update_latest() {
  local nodeid="$1"
  local node_dir="$FIRMWARE_DIR/$nodeid"
  local new_bin="build/ultralights.bin"

  mkdir -p "$node_dir"

  # Find highest existing 1.x.bin
  local highest=0
  local f num
  for f in "$node_dir"/1.*.bin; do
    [[ -f "$f" ]] || continue
    num="${f##*1.}"
    num="${num%.bin}"
    if [[ "$num" =~ ^[0-9]+$ ]] && (( num > highest )); then
      highest=$num
    fi
  done
  local next=$((highest + 1))

  # Move previous latest.bin -> 1.next.bin if it exists
  if [[ -f "$node_dir/latest.bin" ]]; then
    echo "Archiving previous latest.bin -> 1.${next}.bin"
    sudo mv "$node_dir/latest.bin" "$node_dir/1.${next}.bin"
  else
    echo "No existing latest.bin to archive for $nodeid."
  fi

  # Copy newly built firmware to latest.bin
  if [[ -f "$new_bin" ]]; then
    echo "Updating latest.bin for $nodeid"
    sudo cp "$new_bin" "$node_dir/latest.bin"
  else
    echo "WARNING: $new_bin not found. Skipping latest.bin update for $nodeid."
  fi
}

# --- MAIN LOOP ---
if [[ ! -d "$CONFIG_ROOT" ]]; then
  echo "ERROR: CONFIG_ROOT not found: $CONFIG_ROOT"
  exit 1
fi

for target_dir in "$CONFIG_ROOT"/*/; do
  # Strip trailing slash and leading path to get the target name (esp32|esp32c3|esp32s3|etc.)
  target_name="$(basename "$target_dir")"
  echo "=============================="
  echo "Target: $target_name"
  echo "Directory: $target_dir"

  # Per-target prep
  echo "Running idf.py fullclean ..."
  idf.py fullclean

  echo "Removing local ./build directory safely BEFORE set-target ..."
  safe_rm_build

  echo "Setting IDF target to: $target_name"
  idf.py set-target "$target_name"

  # Iterate configs in this target directory
  configs_found=false
  for config_file in "$target_dir"/sdkconfig.*; do
    configs_found=true
    nodeid="${config_file##*.}"
    echo
    echo "--- Processing node: $nodeid ---"
    echo "Using config: $config_file"

    # Copy sdkconfig in place for this build/flash
    cp "$config_file" sdkconfig

    # Remove build dir before flash as well
    echo "Removing local ./build directory safely BEFORE flashing ..."
    safe_rm_build

    # Flash
    echo "Flashing ($FLASH_CMD) ..."
    # shellcheck disable=SC2086
    $FLASH_CMD

    # Version/rotate artifacts
    archive_and_update_latest "$nodeid"
    echo "--- Done: $nodeid ---"
  done

  if [[ "$configs_found" == false ]]; then
    echo "No sdkconfig.* files found in $target_dir (skipping)."
  fi

  echo "Finished target: $target_name"
  echo
done

echo "All targets processed."

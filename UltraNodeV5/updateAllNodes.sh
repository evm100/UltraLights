#!/usr/bin/env bash
# UltraLights multi-target bulk flasher + versioned artifact rotator

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd -P)"

# --- CONFIG ---
CONFIG_ROOT="${CONFIG_ROOT:-../../Configs}"
FIRMWARE_DIR="${PROJECT_ROOT}/firmware"
FIRMWARE_ARCHIVE_DIR="${PROJECT_ROOT}/firmware_artifacts"

# Ensure firmware is always stored inside the project tree, regardless of
# previous environment overrides that pointed to the public symlink directory.
mkdir -p "${FIRMWARE_DIR}"

mkdir -p "${FIRMWARE_ARCHIVE_DIR}"

# --- VERSION INPUT ---
# Pass the desired firmware version as the first argument, or the script will prompt for it.
FIRMWARE_VERSION="${1:-}"
if [[ -z "$FIRMWARE_VERSION" ]]; then
  read -rp "Enter firmware version for manifest files: " FIRMWARE_VERSION
fi
if [[ -z "$FIRMWARE_VERSION" ]]; then
  echo "ERROR: Firmware version is required" >&2
  exit 1
fi

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
echo "Firmware version for manifests: $FIRMWARE_VERSION"
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

copy_file_to_private_archive() {
  local src="$1"
  local dest="$2"
  local dest_dir
  dest_dir="$(dirname "$dest")"

  mkdir -p "$dest_dir"

  local tmp
  tmp="$(mktemp "$dest_dir/.tmp.XXXXXX")"

  if [[ -r "$src" ]]; then
    cat "$src" >"$tmp"
  else
    sudo cat "$src" >"$tmp"
  fi

  chmod 644 "$tmp" 2>/dev/null || sudo chmod 644 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$dest"
}

remove_file_with_sudo() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    return
  fi

  if rm -f "$path" 2>/dev/null; then
    return
  fi

  sudo rm -f "$path"
}

migrate_legacy_archives() {
  local nodeid="$1"
  local node_dir="$FIRMWARE_DIR/$nodeid"
  local archive_node_dir="$FIRMWARE_ARCHIVE_DIR/$nodeid"

  mkdir -p "$archive_node_dir"

  local legacy_file
  for legacy_file in "$node_dir"/*.bin; do
    local base
    base="$(basename "$legacy_file")"

    if [[ "$base" == "latest.bin" ]]; then
      continue
    fi

    echo "Migrating legacy firmware artifact $base for $nodeid to private archive"
    copy_file_to_private_archive "$legacy_file" "$archive_node_dir/$base"
    remove_file_with_sudo "$legacy_file"
  done
}

archive_and_update_latest() {
  local nodeid="$1"
  local node_dir="$FIRMWARE_DIR/$nodeid"
  local archive_node_dir="$FIRMWARE_ARCHIVE_DIR/$nodeid"
  local new_bin="build/ultralights.bin"
  local manifest_path="$node_dir/manifest.json"

  mkdir -p "$node_dir"
  mkdir -p "$archive_node_dir"

  migrate_legacy_archives "$nodeid"

  local previous_version=""
  if [[ -f "$manifest_path" ]]; then
    previous_version=$(python3 - "$manifest_path" <<'PY'
import json
import sys

path = sys.argv[1]

try:
    with open(path, 'r', encoding='utf-8') as fp:
        data = json.load(fp)
except Exception:
    sys.exit(0)

version = data.get("version")
if version is None:
    sys.exit(0)

if not isinstance(version, str):
    version = str(version)

print(version.strip())
PY
)
  fi

  # Move previous latest.bin into the private archive if it exists
  if [[ -f "$node_dir/latest.bin" ]]; then
    if [[ -n "$previous_version" ]]; then
      local safe_prev_version
      safe_prev_version="$(sanitize_version "$previous_version")"

      if [[ -n "$safe_prev_version" ]]; then
        local archived_path
        archived_path="$archive_node_dir/${safe_prev_version}.bin"

        echo "Archiving previous latest.bin for $nodeid -> ${safe_prev_version}.bin (version $previous_version) in private storage"
        copy_file_to_private_archive "$node_dir/latest.bin" "$archived_path"
      else
        echo "WARNING: Previous manifest version for $nodeid sanitized to empty; skipping archive rename."
      fi
    else
      echo "No previous manifest version found for $nodeid; skipping archive rename."
    fi
  else
    echo "No existing latest.bin to archive for $nodeid."
  fi

  # Copy newly built firmware to latest.bin
  if [[ -f "$new_bin" ]]; then
    echo "Updating latest.bin for $nodeid"
    sudo cp "$new_bin" "$node_dir/latest.bin"
    sudo chmod 644 "$node_dir/latest.bin" 2>/dev/null || true

    local safe_current_version
    safe_current_version="$(sanitize_version "$FIRMWARE_VERSION")"
    if [[ -n "$safe_current_version" ]]; then
      local current_archive_path
      current_archive_path="$archive_node_dir/${safe_current_version}.bin"
      echo "Saving firmware build for $nodeid version $FIRMWARE_VERSION to private archive"
      copy_file_to_private_archive "$new_bin" "$current_archive_path"
    else
      echo "WARNING: Firmware version '$FIRMWARE_VERSION' sanitized to empty; skipping private archive copy."
    fi
  else
    echo "WARNING: $new_bin not found. Skipping latest.bin update for $nodeid."
  fi
}

sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    python3 - "$file" <<'PY'
import hashlib
import sys

path = sys.argv[1]
h = hashlib.sha256()
with open(path, 'rb') as fp:
    for chunk in iter(lambda: fp.read(1024 * 1024), b''):
        h.update(chunk)
print(h.hexdigest())
PY
  fi
}

sanitize_version() {
  local version="$1"
  # Allow alphanumeric, dot, underscore and dash in filenames. Replace others with underscore.
  printf '%s' "$version" | sed 's/[^A-Za-z0-9._-]/_/g'
}

update_sdkconfig_version() {
  local sdkconfig_path="$1"
  local version="$2"

  python3 - "$sdkconfig_path" "$version" <<'PY'
import sys
from pathlib import Path

sdkconfig_path = Path(sys.argv[1])
version = sys.argv[2].strip()

escaped_version = version.replace("\\", "\\\\").replace('"', '\\"')

lines = sdkconfig_path.read_text().splitlines()

flag_written = False
version_written = False
output = []

for line in lines:
    if line.startswith("CONFIG_APP_PROJECT_VER_FROM_CONFIG"):
        if not flag_written:
            output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")
            flag_written = True
        # Skip duplicate entries
        continue
    if line.startswith("# CONFIG_APP_PROJECT_VER_FROM_CONFIG"):
        if not flag_written:
            output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")
            flag_written = True
        continue
    if line.startswith("CONFIG_APP_PROJECT_VER="):
        if not version_written:
            output.append(f'CONFIG_APP_PROJECT_VER="{escaped_version}"')
            version_written = True
        continue

    output.append(line)

if not flag_written:
    output.append("CONFIG_APP_PROJECT_VER_FROM_CONFIG=y")

if not version_written:
    output.append(f'CONFIG_APP_PROJECT_VER="{escaped_version}"')

sdkconfig_path.write_text("\n".join(output) + "\n")
PY
}

write_manifest() {
  local nodeid="$1"
  local version="$2"
  local node_dir="$FIRMWARE_DIR/$nodeid"
  local latest_bin="$node_dir/latest.bin"

  if [[ ! -f "$latest_bin" ]]; then
    echo "WARNING: Cannot create manifest for $nodeid (missing $latest_bin)" >&2
    return
  fi

  local size
  size=$(wc -c < "$latest_bin")
  local sha
  sha=$(sha256_file "$latest_bin")
  local generated_at
  generated_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  local safe_version
  safe_version="$(sanitize_version "$version")"
  local manifest_json
  manifest_json=$(cat <<JSON
{
  "device_id": "$nodeid",
  "version": "$version",
  "size": $size,
  "sha256_hex": "$sha",
  "binary_url": "latest.bin",
  "generated_at": "$generated_at"
}
JSON
  )

  local manifest_path="$node_dir/manifest.json"
  local manifest_versioned="$node_dir/manifest_${safe_version}.json"
  local tmp
  tmp=$(mktemp)
  printf '%s\n' "$manifest_json" > "$tmp"
  sudo mv "$tmp" "$manifest_path"
  sudo chmod 644 "$manifest_path" 2>/dev/null || true

  tmp=$(mktemp)
  printf '%s\n' "$manifest_json" > "$tmp"
  sudo mv "$tmp" "$manifest_versioned"
  sudo chmod 644 "$manifest_versioned" 2>/dev/null || true
  echo "Updated manifest: $manifest_path (version $version)"
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

    echo "Embedding firmware version into sdkconfig"
    update_sdkconfig_version "sdkconfig" "$FIRMWARE_VERSION"

    # Remove build dir before flash as well
    echo "Removing local ./build directory safely BEFORE flashing ..."
    safe_rm_build

    # Flash
    echo "Flashing ($FLASH_CMD) ..."
    # shellcheck disable=SC2086
    $FLASH_CMD

    # Version/rotate artifacts
    archive_and_update_latest "$nodeid"
    write_manifest "$nodeid" "$FIRMWARE_VERSION"
    echo "--- Done: $nodeid ---"
  done

  if [[ "$configs_found" == false ]]; then
    echo "No sdkconfig.* files found in $target_dir (skipping)."
  fi

  echo "Finished target: $target_name"
  echo
done

echo "All targets processed."

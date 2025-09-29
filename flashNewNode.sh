#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${ROOT_DIR}/Server/.venv/bin/python3"
CLI_SCRIPT="${ROOT_DIR}/tools/firmware_cli/cli.py"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Error: ${PYTHON_BIN} is not executable. Ensure the server virtual environment is set up." >&2
  exit 1
fi

read -rp "Enter firmware version: " version
if [[ -z "${version// }" ]]; then
  echo "Firmware version is required." >&2
  exit 1
fi

export ULTRA_ROOT="${ROOT_DIR}"
node_id="$(${PYTHON_BIN} - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ.get("ULTRA_ROOT", ".")).resolve()
server_root = root / "Server"
if str(server_root) not in sys.path:
    sys.path.insert(0, str(server_root))

from app import database
from app.auth.models import NodeRegistration
from sqlmodel import select

with database.SessionLocal() as session:
    registration = session.exec(
        select(NodeRegistration).order_by(NodeRegistration.created_at.desc())
    ).first()
    if registration is None:
        sys.exit(1)
    print(registration.node_id)
PY
)"
status=$?
unset ULTRA_ROOT

if [[ ${status} -ne 0 || -z "${node_id}" ]]; then
  echo "Unable to determine the most recent node registration." >&2
  exit 1
fi

cd "${ROOT_DIR}"
echo "Flashing node ${node_id} with firmware version ${version}"
exec "${PYTHON_BIN}" "${CLI_SCRIPT}" flash "${node_id}" --firmware-version "${version}"

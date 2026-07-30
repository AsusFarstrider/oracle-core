#!/usr/bin/env bash
set -euo pipefail

satellite_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${satellite_dir}/.venv/bin/python"
if [[ ! -x "${python_bin}" ]]; then
  echo "satellite Python is not executable: ${python_bin}" >&2
  exit 2
fi

if [[ "${1:-}" == "--canonical" ]]; then
  shift
fi
if [[ "$#" -ne 0 ]]; then
  echo "canonical control-service launcher rejects behavior arguments" >&2
  exit 2
fi
for name in ORACLE_SATELLITE_ID ORACLE_SATELLITE_PROJECTION_STORE_ROOT ORACLE_SATELLITE_RUNTIME_COMPATIBILITY_PATH; do
  if [[ -z "${!name:-}" ]]; then
    echo "canonical control-service launcher requires ${name}" >&2
    exit 2
  fi
done

if [[ -z "${XDG_RUNTIME_DIR:-}" ]]; then
  XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export XDG_RUNTIME_DIR
fi

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" && -S "${XDG_RUNTIME_DIR}/bus" ]]; then
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

exec "${python_bin}" "${satellite_dir}/control_service.py"

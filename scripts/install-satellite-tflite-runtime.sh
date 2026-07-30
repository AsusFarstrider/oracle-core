#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/satellite/.venv/bin/python"
VENV_PIP="${ROOT_DIR}/satellite/.venv/bin/pip"
IF_SUPPORTED=0
PACKAGE_SPEC="tflite-runtime"
NUMPY_SPEC="numpy<2"

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--if-supported]

Options:
  --if-supported  Exit successfully with a short message when the current host
                  cannot use the published tflite-runtime wheel.
  --help          Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --if-supported)
      IF_SUPPORTED=1
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -x "${VENV_PYTHON}" ]] || fail "missing satellite venv python: ${VENV_PYTHON}"
[[ -x "${VENV_PIP}" ]] || fail "missing satellite venv pip: ${VENV_PIP}"

compatibility="$("${VENV_PYTHON}" - <<'PY'
import platform
import sys

machine = platform.machine().lower()
version = sys.version_info[:2]

if version < (3, 8) or version > (3, 11):
    print(f"unsupported:python {version[0]}.{version[1]}")
elif machine not in {"x86_64", "aarch64", "armv7l"}:
    print(f"unsupported:arch {machine}")
else:
    print("supported")
PY
)"

if [[ "${compatibility}" != "supported" ]]; then
  if [[ "${IF_SUPPORTED}" -eq 1 ]]; then
    log "Skipping tflite-runtime install (${compatibility})"
    exit 0
  fi
  fail "current host is not compatible with the published ${PACKAGE_SPEC} wheel (${compatibility})"
fi

log "Installing ${NUMPY_SPEC} and ${PACKAGE_SPEC} into satellite venv"
"${VENV_PIP}" install "${NUMPY_SPEC}" "${PACKAGE_SPEC}"

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SATELLITE_DIR="${ROOT_DIR}/satellite"
VENV_DIR="${SATELLITE_DIR}/.venv"

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

apt_package_available() {
  local package_name="$1"
  apt-cache show "${package_name}" 2>/dev/null | grep -q '^Package: '
}

pick_python() {
  local candidates=()
  local candidate=""
  candidates=(python3.12 python3.11 python3.10 python3.9 python3)
  for candidate in "${candidates[@]}"; do
    if ! have_cmd "${candidate}"; then
      continue
    fi
    if "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
major, minor = sys.version_info[:2]
raise SystemExit(0 if (major, minor) >= (3, 9) else 1)
PY
    then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

python_supports_tflite_runtime() {
  local python_bin="$1"
  "${python_bin}" - <<'PY'
import sys
major, minor = sys.version_info[:2]
raise SystemExit(0 if (3, 8) <= (major, minor) <= (3, 11) else 1)
PY
}

if ! have_cmd apt-get; then
  fail "This bootstrap script currently supports Debian/apt-based systems only"
fi

export DEBIAN_FRONTEND=noninteractive

log "Updating apt metadata"
sudo apt-get update

blas_dev_package=""
for package_name in libatlas-base-dev libopenblas-dev; do
  if apt_package_available "${package_name}"; then
    blas_dev_package="${package_name}"
    break
  fi
done

if [[ -z "${blas_dev_package}" ]]; then
  fail "No supported BLAS development package found (tried libatlas-base-dev, libopenblas-dev)"
fi

log "Installing satellite system packages"
sudo apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  build-essential \
  ffmpeg \
  libportaudio2 \
  portaudio19-dev \
  "${blas_dev_package}" \
  libsndfile1

PYTHON_BIN="$(pick_python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  fail "No supported Python interpreter found. Oracle satellites currently require Python 3.9 or newer."
fi

log "Using Python interpreter: ${PYTHON_BIN}"

log "Creating virtual environment at ${VENV_DIR}"
rm -rf "${VENV_DIR}"
"${PYTHON_BIN}" -m venv "${VENV_DIR}"

log "Upgrading packaging tools"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

log "Installing satellite Python requirements"
if python_supports_tflite_runtime "${VENV_DIR}/bin/python"; then
  "${VENV_DIR}/bin/pip" install -r "${SATELLITE_DIR}/requirements.txt"
else
  log "Current Python does not support published tflite-runtime wheels; installing ONNX-only wake stack"
  "${VENV_DIR}/bin/pip" install \
    numpy==2.4.2 \
    requests==2.32.5 \
    rfc8785==0.1.4 \
    sounddevice==0.5.3 \
    'onnxruntime>=1.25.1,<2' \
    'tqdm<5' \
    'scipy<2' \
    'scikit-learn<2'
  "${VENV_DIR}/bin/pip" install --no-deps openwakeword==0.6.0
fi

log "Installing optional TFLite runtime when supported"
"${ROOT_DIR}/scripts/install-satellite-tflite-runtime.sh" --if-supported

log "Ensuring a long-form player is available"
"${ROOT_DIR}/scripts/install-satellite-player.sh" auto

log "Bootstrap complete"
log "Python: $("${VENV_DIR}/bin/python" --version 2>&1)"
log "Player availability:"
if have_cmd ffplay; then
  log "  ffplay: $(command -v ffplay)"
fi
if have_cmd mpv; then
  log "  mpv: $(command -v mpv)"
fi

cat <<EOF

Next steps:
1. Activate the venv when running manually:
   . "${VENV_DIR}/bin/activate"
2. Configure long-form control commands in the satellite control service:
   python ${SATELLITE_DIR}/longform_player.py play --help
3. Restart the relevant satellite services after updating environment files.

EOF

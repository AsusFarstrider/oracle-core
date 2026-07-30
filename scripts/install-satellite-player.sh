#!/usr/bin/env bash
set -euo pipefail

PREFER="${1:-auto}"

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

log() {
  printf '%s\n' "$*"
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

if [[ "${PREFER}" != "auto" && "${PREFER}" != "ffplay" && "${PREFER}" != "mpv" ]]; then
  fail "usage: $0 [auto|ffplay|mpv]"
fi

if have_cmd ffplay; then
  log "ffplay already installed at $(command -v ffplay)"
  exit 0
fi

if have_cmd mpv; then
  log "mpv already installed at $(command -v mpv)"
  exit 0
fi

if ! have_cmd apt-get; then
  fail "apt-get is required for automatic player installation on this host"
fi

PACKAGE=""
if [[ "${PREFER}" == "ffplay" ]]; then
  PACKAGE="ffmpeg"
elif [[ "${PREFER}" == "mpv" ]]; then
  PACKAGE="mpv"
else
  PACKAGE="ffmpeg"
fi

log "Installing ${PACKAGE} for Oracle long-form playback support"
sudo apt-get update
sudo apt-get install -y "${PACKAGE}"

if have_cmd ffplay; then
  log "Installed ffplay at $(command -v ffplay)"
  exit 0
fi

if have_cmd mpv; then
  log "Installed mpv at $(command -v mpv)"
  exit 0
fi

fail "installation completed but no supported player was found"

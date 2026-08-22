#!/usr/bin/env bash

set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8011}"
TEXT="${2:-what time is it}"

curl -sS \
  -X POST \
  "${BASE_URL}/api/conversation/command" \
  -H "Content-Type: application/json" \
  -d "{
    \"text\": \"${TEXT}\",
    \"source\": \"shell-test\",
    \"session_id\": \"manual-test\"
  }"

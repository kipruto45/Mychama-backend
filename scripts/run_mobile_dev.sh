#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if [[ ! -x "./venv/bin/python" ]]; then
  echo "Missing virtualenv at ${PROJECT_ROOT}/venv. Create it before starting the backend." >&2
  exit 1
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.development}"

exec ./venv/bin/python manage.py runserver 0.0.0.0:8000

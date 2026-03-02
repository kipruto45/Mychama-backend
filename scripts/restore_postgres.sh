#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup-file(.sql.gz|.sql.gz.enc)>" >&2
  exit 1
fi

backup_file="$1"
if [[ ! -f "${backup_file}" ]]; then
  echo "Backup file not found: ${backup_file}" >&2
  exit 1
fi

POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-digital_chama}"
POSTGRES_USER="${POSTGRES_USER:-chama_app}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
BACKUP_ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-}"

if [[ -z "${POSTGRES_PASSWORD}" ]]; then
  echo "POSTGRES_PASSWORD is required." >&2
  exit 1
fi

work_file="${backup_file}"
tmp_decrypted=""

if [[ "${backup_file}" == *.enc ]]; then
  if [[ -z "${BACKUP_ENCRYPTION_KEY}" ]]; then
    echo "BACKUP_ENCRYPTION_KEY is required to restore encrypted backups." >&2
    exit 1
  fi
  tmp_decrypted="$(mktemp /tmp/chama_restore_XXXXXX.sql.gz)"
  openssl enc -d -aes-256-cbc -pbkdf2 \
    -in "${backup_file}" \
    -out "${tmp_decrypted}" \
    -pass "pass:${BACKUP_ENCRYPTION_KEY}"
  work_file="${tmp_decrypted}"
fi

cleanup() {
  if [[ -n "${tmp_decrypted}" ]]; then
    rm -f "${tmp_decrypted}"
  fi
}
trap cleanup EXIT

export PGPASSWORD="${POSTGRES_PASSWORD}"
if [[ "${work_file}" == *.gz ]]; then
  gunzip -c "${work_file}" | psql \
    --host "${POSTGRES_HOST}" \
    --port "${POSTGRES_PORT}" \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}"
else
  psql \
    --host "${POSTGRES_HOST}" \
    --port "${POSTGRES_PORT}" \
    --username "${POSTGRES_USER}" \
    --dbname "${POSTGRES_DB}" \
    -f "${work_file}"
fi

echo "Restore completed into database: ${POSTGRES_DB}"

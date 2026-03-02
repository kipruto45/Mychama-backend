#!/usr/bin/env bash
set -euo pipefail

OUTPUT_DIR="${1:-backups}"
mkdir -p "${OUTPUT_DIR}"

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

timestamp="$(date +%Y%m%d_%H%M%S)"
base_name="backup_${POSTGRES_DB}_${timestamp}.sql.gz"
backup_path="${OUTPUT_DIR}/${base_name}"

export PGPASSWORD="${POSTGRES_PASSWORD}"
pg_dump \
  --host "${POSTGRES_HOST}" \
  --port "${POSTGRES_PORT}" \
  --username "${POSTGRES_USER}" \
  --format=plain \
  --no-owner \
  --no-privileges \
  "${POSTGRES_DB}" | gzip -c > "${backup_path}"

if [[ -n "${BACKUP_ENCRYPTION_KEY}" ]]; then
  encrypted_path="${backup_path}.enc"
  openssl enc -aes-256-cbc -pbkdf2 -salt \
    -in "${backup_path}" \
    -out "${encrypted_path}" \
    -pass "pass:${BACKUP_ENCRYPTION_KEY}"
  rm -f "${backup_path}"
  backup_path="${encrypted_path}"
fi

echo "Backup completed: ${backup_path}"

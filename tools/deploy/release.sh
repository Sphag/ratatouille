#!/usr/bin/env bash
set -euo pipefail
cd "${RATATOUILLE_DEPLOY_ROOT:-/opt/ratatouille}"
version=${1:?Pass the tested 40-character commit SHA}
[[ "$version" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid release SHA' >&2; exit 1; }
export RATATOUILLE_VERSION="$version"
compose=(docker compose --env-file deploy/.env -f compose.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" stop web bot
if "${compose[@]}" run --rm --no-deps migrate test -f /data/ratatouille.sqlite3; then
  backup_name="pre-$version-$(date -u +%Y%m%dT%H%M%S)-$$.sqlite3"
  if ! "${compose[@]}" run --rm --no-deps migrate python -m ratatouille.backup "/data/backups/$backup_name"; then
    "${compose[@]}" start web bot
    echo 'Backup failed; previous services restarted without migration.' >&2
    exit 1
  fi
fi
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" up -d --no-build web bot caddy
for attempt in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1:8000/api/ready >/dev/null; then
    mkdir -p deploy/.release
    printf '%s\n' "$version" > deploy/.release/current
    echo 'Release ready; verify HTTPS and Telegram separately.'
    exit 0
  fi
  sleep 1
done
echo 'Readiness failed. Services and backup retained; follow rollback runbook.' >&2
exit 1

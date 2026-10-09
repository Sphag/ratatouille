#!/usr/bin/env bash
set -euo pipefail
image=${1:?Pass the image tag}
container="ratatouille-ci-web-$$"
volume="ratatouille-ci-data-$$"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker volume create "$volume" >/dev/null
docker run --rm -v "$volume:/data" "$image" python -m ratatouille.database
docker run -d --name "$container" -p 127.0.0.1::8000 -v "$volume:/data" -e BOT_TOKEN=test-only-token -e RATATOUILLE_APP_URL=https://app.example.invalid -e RATATOUILLE_OWNER_TELEGRAM_ID=101 "$image" >/dev/null
port=$(docker port "$container" 8000/tcp | head -n1)
for attempt in $(seq 1 30); do
  if curl --fail --silent "http://$port/api/ready" >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent "http://$port/api/ready"
docker exec -i "$container" python - <<'PY'
from ratatouille.database import database_path, make_engine
from ratatouille.storage import Store
from ratatouille.domain import Nutrition
store=Store(make_engine(database_path()))
owner=store.create_user(telegram_id=101)
store.save_goals(owner,Nutrition(calories='2000',protein='100',fat='70',carbs='250'))
PY
docker restart "$container" >/dev/null
docker exec -i "$container" python - <<'PY'
from ratatouille.database import database_path,make_engine
from ratatouille.storage import Store
from ratatouille.backup import backup
from pathlib import Path
store=Store(make_engine(database_path()))
assert str(store.get_goals(store.create_user(telegram_id=101)).calories)=='2000'
backup(database_path(),Path('/data/backups/smoke.sqlite3'))
PY
docker inspect --format '{{.Config.User}}' "$container" | grep -qx '10001:10001'

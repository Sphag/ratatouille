#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_root"
bash tools/dev/install-uv.sh
bash tools/dev/uv.sh sync --locked
export npm_config_cache="$project_root/tools/dev/.cache/npm"
npm --prefix frontend ci

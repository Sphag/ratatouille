#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project_root"
bash tools/dev/uv.sh run --locked ruff check
bash tools/dev/uv.sh run --locked ruff format --check
bash tools/dev/uv.sh run --locked mypy
bash tools/dev/uv.sh run --locked pytest
npm --prefix frontend run check
npm --prefix frontend run build

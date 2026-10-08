#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
uv_binary="$project_root/tools/dev/.cache/uv-0.12.23/uv"
if [[ ! -x "$uv_binary" ]]; then
  echo 'Run bash tools/dev/install-uv.sh first.' >&2
  exit 1
fi
export UV_CACHE_DIR="$project_root/tools/dev/.cache/uv-cache"
export UV_PYTHON_INSTALL_DIR="$project_root/tools/dev/.cache/python"
export UV_PYTHON_BIN_DIR="$project_root/tools/dev/.cache/python-bin"
export UV_PYTHON_PREFERENCE=only-managed
cd "$project_root"
exec "$uv_binary" "$@"

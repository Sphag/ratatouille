#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/node-env.sh"
cd "$script_dir"
export PLAYWRIGHT_BROWSERS_PATH="$script_dir/.cache/browsers"
case "${1:-}" in
  playwright)
    shift
    [[ -f "$script_dir/.cache/chromium-path" ]] || { echo 'Выполните bash tools/mcp/setup.sh для установки Chromium.' >&2; exit 1; }
    browser_path=$(cat "$script_dir/.cache/chromium-path")
    [[ -x "$browser_path" ]] || { echo 'Chromium не найден; повторите setup.sh.' >&2; exit 1; }
    exec node node_modules/@playwright/mcp/cli.js --headless --isolated --browser chromium --executable-path "$browser_path" --output-dir "$script_dir/.cache/playwright" "$@"
    ;;
  sqlite)
    shift
    if [[ -z ${RATATOUILLE_SQLITE_DSN:-} ]]; then
      echo 'Задайте RATATOUILLE_SQLITE_DSN для согласованной тестовой базы.' >&2
      exit 1
    fi
    exec node node_modules/@bytebase/dbhub/dist/index.js --transport stdio --config "$script_dir/dbhub.toml" "$@"
    ;;
  *) echo 'Использование: bash tools/mcp/run.sh playwright|sqlite' >&2; exit 2 ;;
esac

#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_dir/node-env.sh"
cd "$script_dir"
export npm_config_cache="$script_dir/.cache/npm"
export PLAYWRIGHT_BROWSERS_PATH="$script_dir/.cache/browsers"
npm ci --ignore-scripts --no-audit --no-fund
node node_modules/playwright-browser/cli.js install chromium
node --input-type=module -e 'import { chromium } from "playwright-browser"; console.log(chromium.executablePath());' > .cache/chromium-path

#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
uv_version=0.12.23
uv_sha256=9167d72b3319674b6303c4cbe071854bba13ebdf3d76b1a7cbdc175471fb66d6
uv_directory="$project_root/tools/dev/.cache/uv-$uv_version"

if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'This bootstrap supports Linux x86_64 (including WSL). See docs/ARCHITECTURE.md.' >&2
  exit 1
fi
if [[ -x "$uv_directory/uv" ]]; then
  "$uv_directory/uv" --version
  exit 0
fi
mkdir -p "$project_root/tools/dev/.cache"
uv_download=$(mktemp -d "$project_root/tools/dev/.cache/uv-download-XXXXXX")
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 120 \
  "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-x86_64-unknown-linux-gnu.tar.gz" \
  --output "$uv_download/uv.tar.gz"
printf '%s  %s\n' "$uv_sha256" "$uv_download/uv.tar.gz" | sha256sum --check --status
tar -xzf "$uv_download/uv.tar.gz" --directory "$uv_download"
mkdir -p "$uv_directory"
cp "$uv_download/uv-x86_64-unknown-linux-gnu/uv" "$uv_directory/uv"
chmod +x "$uv_directory/uv"
"$uv_directory/uv" --version

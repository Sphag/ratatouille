#!/usr/bin/env bash
set -euo pipefail

version=24.21.0
archive="node-v${version}-linux-x64.tar.xz"
destination="$HOME/.local/opt/node-v${version}"
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo 'Эта установка предназначена для WSL/Linux x86_64.' >&2
  exit 1
fi
for name in node npm npx; do
  link="$HOME/.local/bin/$name"
  if [[ -e "$link" || -L "$link" ]]; then
    [[ $(readlink "$link") == "$destination/bin/$name" ]] || {
      echo "Команда $name уже существует в ~/.local/bin; установка остановлена." >&2
      exit 1
    }
  fi
done
if [[ ! -d "$destination" ]]; then
  download_dir=$(mktemp -d -t ratatouille-node-XXXXXX)
  curl -fL --connect-timeout 15 --max-time 180 "https://nodejs.org/dist/v${version}/${archive}" -o "$download_dir/$archive"
  curl -fL --connect-timeout 15 --max-time 30 "https://nodejs.org/dist/v${version}/SHASUMS256.txt" -o "$download_dir/SHASUMS256.txt"
  (cd "$download_dir" && awk -v filename="$archive" '$2 == filename' SHASUMS256.txt > selected.sha256 && test -s selected.sha256 && sha256sum -c selected.sha256)
  mkdir -p "$HOME/.local/opt" "$HOME/.local/bin"
  tar -xJf "$download_dir/$archive" -C "$HOME/.local/opt"
  mv "$HOME/.local/opt/node-v${version}-linux-x64" "$destination"
  echo "SHA256: $(awk -v filename="$archive" '$2 == filename { print $1 }' "$download_dir/SHASUMS256.txt")"
  echo "Загрузки сохранены в $download_dir"
fi
[[ $("$destination/bin/node" --version) == "v$version" ]]
for name in node npm npx; do
  link="$HOME/.local/bin/$name"
  [[ -L "$link" ]] || ln -s "$destination/bin/$name" "$link"
done
export PATH="$destination/bin:$PATH"
node --version
npm --version

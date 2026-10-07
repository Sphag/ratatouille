#!/usr/bin/env bash
# Подключается командами запуска; stdout остаётся свободным для MCP.
RATATOUILLE_NODE_HOME="$HOME/.local/opt/node-v24.21.0"
if [[ ! -x "$RATATOUILLE_NODE_HOME/bin/node" ]]; then
  echo 'Не найден Node.js 24.21.0. Выполните bash tools/mcp/install-node.sh.' >&2
  return 1
fi
export PATH="$RATATOUILLE_NODE_HOME/bin:$PATH"

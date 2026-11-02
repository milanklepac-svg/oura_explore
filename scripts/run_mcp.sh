#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
# GUI clients sometimes omit /usr/local/bin from PATH.
if [ -z "${DOCKER_BIN:-}" ]; then
  if command -v docker >/dev/null 2>&1; then DOCKER_BIN=$(command -v docker)
  elif [ -x /usr/local/bin/docker ]; then DOCKER_BIN=/usr/local/bin/docker
  elif [ -x /opt/homebrew/bin/docker ]; then DOCKER_BIN=/opt/homebrew/bin/docker
  else echo 'Docker not found. Start Docker Desktop and set DOCKER_BIN.' >&2; exit 1
  fi
fi
# stdout is reserved for MCP. Docker diagnostics remain on stderr.
exec "$DOCKER_BIN" compose --profile mcp run --rm -T --no-deps mcp

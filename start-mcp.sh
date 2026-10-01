#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "${ROOT}"

if ! command -v uv >/dev/null 2>&1; then
  echo "start-mcp: uv is required (https://docs.astral.sh/uv/getting-started/installation/)" >&2
  exit 127
fi

# MCP over stdio reserves stdout for JSON-RPC. All preparation output goes to stderr.
uv sync --frozen --no-dev >&2
uv run --no-sync python second_brain/graph_parts.py compose >&2
uv run --no-sync python -m second_brain.verify_bundle >&2
exec uv run --no-sync python second_brain/server.py

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'start-mcp: uv is required (https://docs.astral.sh/uv/getting-started/installation/)'
}

# MCP over stdio reserves stdout for JSON-RPC.
& uv sync --frozen --no-dev | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& uv run --no-sync python second_brain/graph_parts.py compose | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& uv run --no-sync python -m second_brain.verify_bundle | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& uv run --no-sync python second_brain/server.py
exit $LASTEXITCODE

"""Exercise the documented start script as an MCP stdio client would."""

import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
command = (["pwsh", "-NoProfile", "-File", str(root / "start-mcp.ps1")]
           if sys.platform == "win32" else [str(root / "start-mcp.sh")])
request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "clone-smoke-test", "version": "1"},
    },
}
process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True)
try:
    stdout, stderr = process.communicate(json.dumps(request) + "\n", timeout=90)
except subprocess.TimeoutExpired:
    process.kill()
    process.communicate()
    raise SystemExit("MCP start script did not answer initialize within 90 seconds")
if process.returncode or len(stdout.splitlines()) != 1:
    raise SystemExit(f"MCP stdio failed: exit={process.returncode}, stdout lines={len(stdout.splitlines())}\n{stderr}")
response = json.loads(stdout)
assert response["id"] == 1
assert response["result"]["serverInfo"]["name"] == "Ravenroot Second Brain"
print("MCP stdio initialize OK")

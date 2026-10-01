# Ravenroot Second Brain MCP

Run the Ravenroot knowledge graph as a local [Model Context Protocol](https://modelcontextprotocol.io/) server. The repository contains a read-only graph snapshot generated from [ravenroot-ai/ravenroot](https://github.com/ravenroot-ai/ravenroot), the FastMCP server, and the code that rebuilds the graph. It does not require Google Cloud, Horizon, Qwen, or access to the source repository to answer queries.

## Start locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and clone this repository. On macOS or Linux:

```sh
git clone https://github.com/ravenroot-ai/ravenroot-second-brain.git
cd ravenroot-second-brain
./start-mcp.sh
```

On Windows, run `./start-mcp.ps1` from PowerShell after cloning. Both scripts perform the same verification and start the stdio server.

Use the absolute path to `start-mcp.sh` as the command for an MCP client that launches local **stdio** servers. The first start installs the pinned Python dependencies, rebuilds `second_brain/composed/graph.json`, verifies its SHA-256 against `second_brain/provenance.json`, and starts the server. Subsequent starts reuse the verified graph. Python 3.12 or 3.13 is selected by uv.

For a client on the same machine that requires an HTTP URL, run:

```sh
MCP_TRANSPORT=http MCP_HOST=127.0.0.1 MCP_PORT=8765 ./start-mcp.sh
```

The endpoint is `http://127.0.0.1:8765/mcp`. It has no authentication; keep it bound to loopback unless you add access controls yourself. This repository does not host a shared public endpoint. Each clone runs its own server.

## Snapshot and updates

`second_brain/parts/graph_partN.json` is the committed distribution format. GitHub blocks ordinary Git files above 100 MiB, so the single rebuilt graph is ignored. `second_brain/graph_parts.py compose` joins the parts in order and refuses a result whose SHA-256 differs from the split header. `second_brain/verify_bundle.py` also checks the published provenance. The graph and provenance identify the indexed Ravenroot source commit; they are an immutable snapshot until this repository publishes a new one.

The private graph generation workflow and canonical source graph stay outside this repository. Maintainers publish a newly accepted snapshot by updating **only** the parts and provenance together, then running the tests from a clean checkout before pushing. Never commit `second_brain/graph.json`, provider settings, credentials, local caches, or agent configuration.

## MCP tools

The read-only server provides `graphify_query`, `graphify_path`, `graphify_explain`, `graphify_neighbors`, `graphify_stats`, and `graphify_god_nodes`, plus `graphify://stats` and `graphify://provenance` resources.

## Development

```sh
uv sync --frozen --dev
uv run pytest -q second_brain
```

The code and bundled data are distributed under [Apache License 2.0](LICENSE).

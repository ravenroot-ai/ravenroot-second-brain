"""FastMCP facade for the Ravenroot Graphify knowledge graph.

The distributable snapshot is versioned as ``parts/graph_partN.json``. On
startup the server recomposes and verifies ``composed/graph.json`` when needed.
An owner may select a separately maintained graph with ``GRAPH_PATH``.

The graph is loaded lazily and retained for the lifetime of the MCP process.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

import networkx as nx
from fastmcp import FastMCP
from graphify.analyze import god_nodes as _god_nodes
from graphify.build import edge_data
from graphify.security import sanitize_label
from graphify.serve import (
    _cut_lines_to_budget,
    _find_node,
    _load_graph,
    _query_graph_text,
    _shortest_path_text,
    find_node_ambiguity,
)

try:
    from second_brain import graph_parts
except ImportError:  # executed as a script: second_brain/ itself is on sys.path
    import graph_parts

HERE = Path(__file__).resolve().parent
GRAPH_PATH = Path(os.environ.get("GRAPH_PATH", graph_parts.DEFAULT_COMPOSED)).resolve()
PROVENANCE_PATH = Path(os.environ.get("PROVENANCE_PATH", HERE / "provenance.json")).resolve()


@lru_cache(maxsize=1)
def ensure_graph() -> Path:
    """Recompose the default graph from its committed parts when it is missing or stale.

    An explicit GRAPH_PATH elsewhere is served as given.
    """
    if GRAPH_PATH == graph_parts.DEFAULT_COMPOSED.resolve():
        graph_parts.ensure_composed(graph_parts.DEFAULT_PARTS_DIR, GRAPH_PATH)
    return GRAPH_PATH


@asynccontextmanager
async def _compose_on_startup(_server: FastMCP):
    ensure_graph()
    yield {}


mcp = FastMCP(
    name="Ravenroot Second Brain",
    instructions=(
        "Knowledge graph for Ravenroot architecture and source code. Use "
        "graphify_query first for architecture questions, graphify_explain for "
        "a precise symbol, and graphify_path to connect two concepts."
    ),
    lifespan=_compose_on_startup,
)

_GRAPH_LOCK = threading.RLock()


def _bounded(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


@lru_cache(maxsize=1)
def _context() -> tuple[nx.Graph, dict[int, list[str]]]:
    """Load and index the immutable deployed graph once per worker."""
    graph = _load_graph(str(ensure_graph()))
    communities: dict[int, list[str]] = {}
    for node_id, data in graph.nodes(data=True):
        community_id = data.get("community")
        if community_id is not None:
            communities.setdefault(int(community_id), []).append(node_id)
    return graph, communities


@lru_cache(maxsize=1)
def _provenance() -> dict:
    return json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))


def _filters(kind: str) -> list[str] | None:
    if kind == "call":
        return ["call"]
    if kind == "imports":
        return ["import"]
    return None


def _query(
    question: str,
    budget: int = 800,
    context_filter: str = "all",
    mode: str = "bfs",
    depth: int = 3,
) -> str:
    if not question.strip():
        raise ValueError("question must not be empty")
    with _GRAPH_LOCK:
        graph, _ = _context()
        return _query_graph_text(
            graph,
            question.strip(),
            mode=mode,
            depth=_bounded(depth, 1, 6),
            token_budget=_bounded(budget, 200, 4000),
            context_filters=_filters(context_filter),
            graph_path=str(GRAPH_PATH),
        )


@mcp.tool
def graphify_query(
    question: str,
    budget: int = 800,
    context_filter: Literal["all", "call", "imports"] = "all",
    mode: Literal["bfs", "dfs"] = "bfs",
    depth: int = 3,
) -> str:
    """Query Ravenroot's Graphify graph using natural language or symbol names.

    Args:
        question: Architecture question, keyword, source path, or symbol name.
        budget: Approximate response token budget (200-4000).
        context_filter: Limit edges to calls, imports, or use all relations.
        mode: BFS for broad context or DFS for a specific trace.
        depth: Traversal depth from 1 to 6.
    """
    return _query(question, budget, context_filter, mode, depth)


@mcp.tool
def graphify_path(
    from_symbol: str,
    to_symbol: str,
    max_hops: int = 8,
    undirected: bool = False,
) -> str:
    """Find the shortest Graphify path between two Ravenroot concepts.

    Direction is respected by default. Set undirected to true when the goal is
    simply to discover whether and how two concepts are connected.
    """
    if not from_symbol.strip() or not to_symbol.strip():
        raise ValueError("from_symbol and to_symbol must not be empty")
    arguments = {
        "source": from_symbol.strip(),
        "target": to_symbol.strip(),
        "max_hops": _bounded(max_hops, 1, 32),
        "undirected": undirected,
    }
    with _GRAPH_LOCK:
        graph, _ = _context()
        return _shortest_path_text(graph, arguments)


def _explain(symbol: str, budget: int = 1600) -> str:
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    with _GRAPH_LOCK:
        graph, _ = _context()
        matches = _find_node(graph, symbol.strip())
        if not matches:
            return f"No node matching '{symbol}' found."
        rivals = find_node_ambiguity(graph, symbol.strip())
        if rivals:
            lines = [f"Ambiguous: '{symbol}' matches nodes in different files:"]
            lines.extend(
                f"  {graph.nodes[node].get('source_file') or node}\n    id: {node}"
                for node in rivals
            )
            lines.append("Retry with the repo-relative path or the full node id.")
            return "\n".join(lines)

        node_id = matches[0]
        data = graph.nodes[node_id]
        lines = [
            f"Node: {sanitize_label(str(data.get('label', node_id)))}",
            f"  ID: {sanitize_label(str(node_id))}",
            (
                "  Source: "
                f"{sanitize_label(str(data.get('source_file', '')))} "
                f"{sanitize_label(str(data.get('source_location', '')))}"
            ),
            f"  Type: {sanitize_label(str(data.get('file_type', '')))}",
            (
                "  Community: "
                f"{sanitize_label(str(data.get('community_name') or data.get('community', '')))}"
            ),
            f"  Degree: {graph.degree(node_id)}",
        ]

        connections: list[tuple[str, str, dict]] = []
        for neighbor in graph.successors(node_id):
            details = edge_data(graph, node_id, neighbor)
            direction = "out" if details.get("_src", node_id) == node_id else "in"
            connections.append((direction, neighbor, details))
        for neighbor in graph.predecessors(node_id):
            details = edge_data(graph, neighbor, node_id)
            direction = "in" if details.get("_src", neighbor) == neighbor else "out"
            connections.append((direction, neighbor, details))

        if connections:
            lines.append(f"Connections ({len(connections)}):")
            connections.sort(key=lambda item: graph.degree(item[1]), reverse=True)
            for direction, neighbor, details in connections:
                arrow = "-->" if direction == "out" else "<--"
                relation = sanitize_label(str(details.get("relation", "")))
                confidence = sanitize_label(str(details.get("confidence", "")))
                source_file = sanitize_label(str(details.get("source_file", "")))
                location = sanitize_label(str(details.get("source_location", "")))
                site = f" at={source_file}:{location}" if location else ""
                label = sanitize_label(str(graph.nodes[neighbor].get("label", neighbor)))
                lines.append(f"  {arrow} {label} [{relation}] [{confidence}]{site}")

        return _cut_lines_to_budget(
            lines,
            _bounded(budget, 200, 4000),
            "Raise budget or query a more specific symbol or path.",
        )


@mcp.tool
def graphify_explain(symbol: str, budget: int = 1600) -> str:
    """Explain one precise Graphify node and all of its direct connections."""
    return _explain(symbol, budget)


@mcp.tool
def graphify_neighbors(
    symbol: str,
    relation_filter: str = "",
    budget: int = 1600,
) -> str:
    """List direct incoming and outgoing neighbors of a Graphify node."""
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    with _GRAPH_LOCK:
        graph, _ = _context()
        matches = _find_node(graph, symbol.strip())
        if not matches:
            return f"No node matching '{symbol}' found."
        rivals = find_node_ambiguity(graph, symbol.strip())
        if rivals:
            return _explain(symbol, budget)

        node_id = matches[0]
        node_label = sanitize_label(str(graph.nodes[node_id].get("label", node_id)))
        lines = [f"Neighbors of {node_label}:"]
        wanted = relation_filter.lower().strip()
        for direction, iterator in (
            ("-->", graph.successors(node_id)),
            ("<--", graph.predecessors(node_id)),
        ):
            for neighbor in iterator:
                source, target = (node_id, neighbor) if direction == "-->" else (neighbor, node_id)
                details = edge_data(graph, source, target)
                relation = str(details.get("relation", ""))
                if wanted and wanted not in relation.lower():
                    continue
                label = sanitize_label(str(graph.nodes[neighbor].get("label", neighbor)))
                lines.append(
                    f"  {direction} {label} "
                    f"[{sanitize_label(relation)}] "
                    f"[{sanitize_label(str(details.get('confidence', '')))}]"
                )
        return _cut_lines_to_budget(
            lines,
            _bounded(budget, 200, 4000),
            "Narrow with relation_filter or query a specific symbol.",
        )


def _stats_text() -> str:
    with _GRAPH_LOCK:
        graph, communities = _context()
        provenance = _provenance()
        confidence = [data.get("confidence", "EXTRACTED") for _, _, data in graph.edges(data=True)]
        total = len(confidence) or 1
        return "\n".join(
            [
                f"Nodes: {graph.number_of_nodes()}",
                f"Edges: {graph.number_of_edges()}",
                f"Communities: {len(communities)}",
                f"EXTRACTED: {round(confidence.count('EXTRACTED') / total * 100)}%",
                f"INFERRED: {round(confidence.count('INFERRED') / total * 100)}%",
                f"AMBIGUOUS: {round(confidence.count('AMBIGUOUS') / total * 100)}%",
                f"Built from commit: {provenance['built_at_commit']}",
                f"Graph SHA-256: {provenance['sha256']}",
            ]
        )


@mcp.tool
def graphify_stats() -> str:
    """Return deployed graph counts, confidence mix, source commit, and checksum."""
    return _stats_text()


@mcp.tool
def graphify_god_nodes(top_n: int = 10) -> str:
    """Return the most connected core abstractions in the Ravenroot graph."""
    with _GRAPH_LOCK:
        graph, _ = _context()
        nodes = _god_nodes(graph, top_n=_bounded(top_n, 1, 50))
        lines = ["God nodes (most connected):"]
        lines.extend(
            f"  {index}. {sanitize_label(str(node['label']))} - {node['degree']} edges"
            for index, node in enumerate(nodes, 1)
        )
        return "\n".join(lines)


@mcp.resource("graphify://stats")
def graphify_stats_resource() -> str:
    """Machine-readable entry point for deployed graph statistics."""
    return _stats_text()


@mcp.resource("graphify://provenance")
def graphify_provenance_resource() -> str:
    """Provenance and checksum of the graph bundled with this deployment."""
    return json.dumps(_provenance(), indent=2, ensure_ascii=False)


if __name__ == "__main__":
    # stdio stays the default so first-party clients (Claude Code, VS Code) keep
    # spawning this file as a subprocess. Containerised clients such as Open WebUI
    # cannot spawn a host process, so MCP_TRANSPORT=http serves the same tools over
    # streamable HTTP at http://MCP_HOST:MCP_PORT/mcp instead.
    _transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if _transport == "stdio":
        mcp.run()
    else:
        mcp.run(
            transport=_transport,
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8765")),
        )

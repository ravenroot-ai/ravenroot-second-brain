import json
from pathlib import Path

import pytest

from second_brain import graph_parts, server


@pytest.fixture(scope="module", autouse=True)
def composed_graph() -> None:
    server.ensure_graph()


def test_composed_graph_is_byte_identical_to_split_source() -> None:
    provenance = json.loads(server.PROVENANCE_PATH.read_text(encoding="utf-8"))
    assert graph_parts.expected_sha256() == provenance["sha256"]
    assert graph_parts._sha256(Path(server.GRAPH_PATH)) == provenance["sha256"]
    assert all(
        path.stat().st_size <= graph_parts.DEFAULT_MAX_BYTES
        for path in graph_parts._part_files(graph_parts.DEFAULT_PARTS_DIR)
    )


def test_bundled_graph_matches_provenance() -> None:
    graph_path = Path(server.GRAPH_PATH)
    provenance = json.loads(server.PROVENANCE_PATH.read_text(encoding="utf-8"))
    data = json.loads(graph_path.read_text(encoding="utf-8"))

    assert graph_path.stat().st_size == provenance["bytes"]
    assert len(data["nodes"]) == provenance["nodes"]
    assert len(data["links"]) == provenance["relationships"]
    assert data["built_at_commit"] == provenance["built_at_commit"]
    assert not any(".ravenroot-worktrees" in str(node.get("source_file", "")) for node in data["nodes"])


def test_graphify_query_and_explain_use_bundled_graph() -> None:
    provenance = json.loads(server.PROVENANCE_PATH.read_text(encoding="utf-8"))
    graph = json.loads(Path(server.GRAPH_PATH).read_text(encoding="utf-8"))
    graph_manager_id = next(
        node["id"]
        for node in graph["nodes"]
        if node.get("label") == "GraphManager"
        and str(node.get("source_file", "")).endswith("/GraphManager.java")
    )
    query = server._query("GraphManager", budget=400, depth=1)
    explanation = server._explain(
        graph_manager_id,
        budget=400,
    )

    assert "GraphManager" in query
    assert f"{provenance['nodes']} nodes" in query
    assert "Node: GraphManager" in explanation
    assert "GraphManager.java" in explanation


def test_stats_match_graph() -> None:
    provenance = json.loads(server.PROVENANCE_PATH.read_text(encoding="utf-8"))
    stats = server._stats_text()

    assert f"Nodes: {provenance['nodes']}" in stats
    assert f"Edges: {provenance['relationships']}" in stats
    assert provenance["sha256"][:16] in stats

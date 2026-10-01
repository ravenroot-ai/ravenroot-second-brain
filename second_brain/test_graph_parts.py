import json
from pathlib import Path

import pytest

from second_brain import graph_parts


def _graph(tmp_path: Path) -> Path:
    graph = {
        "directed": False,
        "multigraph": False,
        "graph": {"hyperedges": [{"id": "h", "nodes": ["n0", "n1"]}]},
        "nodes": [{"id": f"n{i}", "label": f"Node é {i}", "community": i % 3} for i in range(300)],
        "links": [{"source": f"n{i}", "target": f"n{i + 1}", "relation": "calls"} for i in range(299)],
        "hyperedges": [{"id": f"h{i}", "nodes": [f"n{i}"]} for i in range(20)],
        "built_at_commit": "abc123",
    }
    source = tmp_path / "graph.json"
    source.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    return source


def test_split_keeps_every_part_below_the_limit_and_composes_byte_identically(tmp_path: Path) -> None:
    source = _graph(tmp_path)
    max_bytes = 12_000
    parts = graph_parts.split(source, tmp_path / "parts", max_bytes)

    assert len(parts) > 3
    assert all(path.stat().st_size <= max_bytes for path in parts)
    # Greedy packing: every part but the last is filled to within one item of the limit.
    assert all(path.stat().st_size > max_bytes - 400 for path in parts[:-1])
    for number, path in enumerate(parts, 1):
        part = json.loads(path.read_text(encoding="utf-8"))
        assert list(part) == ["_split", *json.loads(source.read_text())]
        assert part["_split"]["part"] == number and part["_split"]["parts"] == len(parts)

    target = graph_parts.compose(tmp_path / "parts", tmp_path / "composed" / "graph.json")
    assert target.read_bytes() == source.read_bytes()
    assert graph_parts.ensure_composed(tmp_path / "parts", target) == target


def test_resplit_removes_parts_that_are_no_longer_needed(tmp_path: Path) -> None:
    source = _graph(tmp_path)
    graph_parts.split(source, tmp_path / "parts", 12_000)
    parts = graph_parts.split(source, tmp_path / "parts", 1_000_000)

    assert [path.name for path in parts] == ["graph_part1.json"]
    assert sorted(path.name for path in (tmp_path / "parts").iterdir()) == ["graph_part1.json"]


def test_compose_refuses_a_missing_or_mismatched_part(tmp_path: Path) -> None:
    source = _graph(tmp_path)
    parts = graph_parts.split(source, tmp_path / "parts", 12_000)
    target = tmp_path / "composed" / "graph.json"

    parts[1].unlink()
    with pytest.raises(ValueError, match="contiguously"):
        graph_parts.compose(tmp_path / "parts", target)
    assert not target.exists()

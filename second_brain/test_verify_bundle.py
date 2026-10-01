import json
from pathlib import Path

import pytest

from second_brain import graph_parts, verify_bundle


def test_verify_bundle_rejects_provenance_from_another_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "graph.json"
    source.write_text(json.dumps({"nodes": [{"id": "a"}], "links": []}, indent=2), encoding="utf-8")
    parts_dir = tmp_path / "parts"
    graph_parts.split(source, parts_dir)
    rebuilt = graph_parts.compose(parts_dir, tmp_path / "composed" / "graph.json")
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps({"sha256": "0" * 64, "bytes": rebuilt.stat().st_size}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="provenance SHA-256"):
        verify_bundle.verify(rebuilt, parts_dir, provenance)


def test_published_bundle_matches_provenance() -> None:
    assert verify_bundle.verify() == graph_parts.expected_sha256()

"""Verify that the rebuilt public graph matches its published provenance."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from second_brain import graph_parts

HERE = Path(__file__).resolve().parent


def verify(
    graph_path: Path = graph_parts.DEFAULT_COMPOSED,
    parts_dir: Path = graph_parts.DEFAULT_PARTS_DIR,
    provenance_path: Path = HERE / "provenance.json",
) -> str:
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    expected = graph_parts.expected_sha256(parts_dir)
    if provenance["sha256"] != expected:
        raise ValueError("provenance SHA-256 differs from the versioned graph parts")
    if graph_path.stat().st_size != provenance["bytes"]:
        raise ValueError("rebuilt graph size differs from provenance")
    if graph_parts._sha256(graph_path) != expected:
        raise ValueError("rebuilt graph SHA-256 differs from provenance")
    return expected


def main() -> int:
    try:
        digest = verify()
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"verify_bundle: {exc}", file=sys.stderr)
        return 1
    print(f"Graph verified: sha256={digest}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Split Graphify's graph.json into GitHub-sized parts and compose it back.

GitHub rejects files above 100 MiB, while Graphify writes one monolithic
graph.json. ``split`` packs the graph greedily into as few parts as possible,
each at most ``--max-bytes`` (default 100 MB, below GitHub's 100 MiB limit):
a 560 MB graph becomes five parts just under 100 MB plus one of about 60 MB.

Every part is a self-contained graph document with the same top-level keys as
the source. List-valued keys (``nodes``, ``links``, ``hyperedges``...) carry a
contiguous slice of the source sequence; every other key is copied verbatim.
A ``_split`` header records the part number, the part count and the SHA-256 of
the source file.

``compose`` concatenates the slices in order and re-emits Graphify's exact
``json.dumps(indent=2)`` layout, so the composed file is byte-identical to the
source and is verified against the recorded SHA-256 before it replaces the
target. It streams one part at a time and never overwrites Graphify's own
graph.json: its default target is ``second_brain/composed/graph.json``.

    python second_brain/graph_parts.py split     # graph.json -> parts/graph_partN.json
    python second_brain/graph_parts.py compose   # parts/ -> composed/graph.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE / "graph.json"
DEFAULT_PARTS_DIR = HERE / "parts"
DEFAULT_COMPOSED = HERE / "composed" / "graph.json"
DEFAULT_MAX_BYTES = 100_000_000

SPLIT_KEY = "_split"
PART_NAME = "graph_part{}.json"
PART_PATTERN = re.compile(r"^graph_part([1-9][0-9]*)\.json$")
# Room for the digits of the final part count, unknown while packing.
_HEADER_SLACK = 64


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _dump_value(value: object, indent: int) -> str:
    """Serialize ``value`` as it appears at ``indent`` spaces inside an indent=2 document."""
    return json.dumps(value, indent=2).replace("\n", "\n" + " " * indent)


def _write_document(handle, keys: list[str], values: dict, lists: dict[str, Iterator[str]]) -> None:
    """Stream an indent=2 JSON object whose list values arrive as pre-serialized items."""
    handle.write("{")
    for position, key in enumerate(keys):
        handle.write(("," if position else "") + "\n  " + json.dumps(key) + ": ")
        if key not in lists:
            handle.write(_dump_value(values[key], 2))
            continue
        empty = True
        for item in lists[key]:
            handle.write(("[" if empty else ",") + "\n    " + item)
            empty = False
        handle.write("[]" if empty else "\n  ]")
    handle.write("\n}")


def _atomic_writer(target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o644)
    # Preserve Graphify's LF-only JSON bytes on Windows as well as Unix.
    return os.fdopen(fd, "w", encoding="utf-8", newline="\n"), Path(tmp)


def split(source: Path, parts_dir: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Path]:
    raw = source.read_bytes()
    if json.dumps(graph := json.loads(raw), indent=2).encode() != raw:
        raise ValueError(f"{source} is not in Graphify's indent=2 layout; refusing a lossy split")
    del raw
    sha = _sha256(source)
    if not isinstance(graph, dict):
        raise ValueError(f"{source} must contain a JSON object")
    keys = list(graph)
    list_keys = [key for key in keys if isinstance(graph[key], list)]
    fixed = {key: graph[key] for key in keys if key not in list_keys}

    def header(part: int, parts: int) -> dict:
        return {"part": part, "parts": parts, "sha256": sha, "keys": keys}

    # An empty part costs its fixed skeleton; every item adds its text plus ",\n    ".
    skeleton = {SPLIT_KEY: header(0, 0), **fixed, **{key: [] for key in list_keys}}
    overhead = len(json.dumps(skeleton, indent=2).encode()) + _HEADER_SLACK
    overhead += sum(len("\n  ]") for _ in list_keys)
    if overhead >= max_bytes:
        raise ValueError(f"max_bytes={max_bytes} cannot even hold the fixed graph keys")

    chunks: list[dict[str, list[str]]] = [{key: [] for key in list_keys}]
    used = overhead
    for key in list_keys:
        for value in graph[key]:
            item = _dump_value(value, 4)
            cost = len(item.encode()) + len(",\n    ")
            if overhead + cost > max_bytes:
                raise ValueError(f"one {key} item alone needs {cost} bytes, above max_bytes={max_bytes}")
            if used + cost > max_bytes:
                chunks.append({k: [] for k in list_keys})
                used = overhead
            chunks[-1][key].append(item)
            used += cost
    del graph

    parts_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for number, chunk in enumerate(chunks, 1):
        target = parts_dir / PART_NAME.format(number)
        handle, tmp = _atomic_writer(target)
        with handle:
            _write_document(
                handle,
                [SPLIT_KEY, *keys],
                {SPLIT_KEY: header(number, len(chunks)), **fixed},
                {key: iter(chunk[key]) for key in list_keys},
            )
        if tmp.stat().st_size > max_bytes:
            tmp.unlink()
            raise AssertionError(f"{target.name} exceeded max_bytes; packing estimate is wrong")
        os.replace(tmp, target)
        written.append(target)

    for stale in parts_dir.iterdir():
        match = PART_PATTERN.match(stale.name)
        if match and int(match.group(1)) > len(chunks):
            stale.unlink()
    return written


def _part_files(parts_dir: Path) -> list[Path]:
    numbered = sorted(
        (int(match.group(1)), path)
        for path in parts_dir.glob("graph_part*.json")
        if (match := PART_PATTERN.match(path.name))
    )
    if not numbered:
        raise FileNotFoundError(f"no graph_partN.json files in {parts_dir}")
    if [number for number, _ in numbered] != list(range(1, len(numbered) + 1)):
        raise ValueError(f"graph parts in {parts_dir} are not numbered contiguously from 1")
    return [path for _, path in numbered]


def _read_header(path: Path) -> dict:
    """Read the ``_split`` header without parsing the whole part."""
    with path.open("r", encoding="utf-8") as handle:
        head = handle.read(64 * 1024)
    match = re.search(r'"_split": (\{.*?\n  \})', head, re.S)
    if not match:
        raise ValueError(f"{path} has no _split header")
    return json.loads(match.group(1))


def expected_sha256(parts_dir: Path = DEFAULT_PARTS_DIR) -> str:
    return _read_header(_part_files(parts_dir)[0])["sha256"]


def compose(parts_dir: Path = DEFAULT_PARTS_DIR, target: Path = DEFAULT_COMPOSED) -> Path:
    paths = _part_files(parts_dir)
    first = _read_header(paths[0])
    keys, sha = first["keys"], first["sha256"]
    for number, path in enumerate(paths, 1):
        header = _read_header(path)
        if header["part"] != number or header["parts"] != len(paths) or header["sha256"] != sha:
            raise ValueError(f"{path.name} does not belong to the same {len(paths)}-part split as graph_part1.json")

    fixed: dict = {}

    def loaded() -> Iterator[dict]:
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            if not fixed:
                fixed.update({key: value for key, value in document.items() if not isinstance(value, list)})
            yield document

    documents = loaded()
    current = next(documents)
    list_keys = [key for key in keys if isinstance(current.get(key), list)]

    def items(key: str) -> Iterator[str]:
        # Slices are contiguous in key order, so each part is parsed exactly once.
        nonlocal current
        while True:
            for value in current[key]:
                yield _dump_value(value, 4)
            later = list_keys[list_keys.index(key) + 1 :]
            if any(current[k] for k in later):
                return
            nxt = next(documents, None)
            if nxt is None:
                return
            current = nxt

    handle, tmp = _atomic_writer(target)
    try:
        with handle:
            _write_document(handle, keys, fixed, {key: items(key) for key in list_keys})
        if (actual := _sha256(tmp)) != sha:
            raise ValueError(f"composed graph SHA-256 {actual} does not match the split source {sha}")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def ensure_composed(parts_dir: Path = DEFAULT_PARTS_DIR, target: Path = DEFAULT_COMPOSED) -> Path:
    """Compose ``target`` unless it already matches the committed parts."""
    if target.is_file() and _sha256(target) == expected_sha256(parts_dir):
        return target
    return compose(parts_dir, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    split_cmd = commands.add_parser("split", help="split graph.json into graph_partN.json files")
    split_cmd.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    split_cmd.add_argument("--parts-dir", type=Path, default=DEFAULT_PARTS_DIR)
    split_cmd.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    compose_cmd = commands.add_parser("compose", help="rebuild graph.json from graph_partN.json files")
    compose_cmd.add_argument("--parts-dir", type=Path, default=DEFAULT_PARTS_DIR)
    compose_cmd.add_argument("--target", type=Path, default=DEFAULT_COMPOSED)
    compose_cmd.add_argument("--force", action="store_true", help="recompose even when the target is current")
    args = parser.parse_args(argv)

    if args.command == "split":
        for path in split(args.source, args.parts_dir, args.max_bytes):
            print(f"{path} {path.stat().st_size} bytes")
    else:
        path = (compose if args.force else ensure_composed)(args.parts_dir, args.target)
        print(f"{path} {path.stat().st_size} bytes sha256={expected_sha256(args.parts_dir)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

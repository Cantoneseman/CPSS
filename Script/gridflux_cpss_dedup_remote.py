#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Compressor.dedup_manifest import read_manifest, rebuild_from_bundle


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def verify_raw_tree(root: Path, expected_path: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    results = []
    expected_paths = set()
    for item in expected:
        rel_path = item["rel_path"]
        expected_paths.add(rel_path)
        path = root / rel_path
        digest = sha256_file(path) if path.is_file() else ""
        ok = path.is_file() and path.stat().st_size == item["size_bytes"] and digest == item["sha256"]
        results.append({
            "rel_path": rel_path,
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": digest,
            "ok": ok,
        })
        if not ok:
            raise ValueError(f"raw tree verification failed: {rel_path}")
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and not path.name.endswith(".gridflux.manifest")
        and not path.name.endswith(".gridflux.tree.upload.manifest")
    }
    unexpected = sorted(actual_paths - expected_paths)
    if unexpected:
        raise ValueError(f"raw tree contains unexpected files: {unexpected[:5]}")
    return {
        "mode": "raw",
        "file_count": len(results),
        "file_results": results,
        "decompress_time_s": 0.0,
        "rebuild_time_s": 0.0,
        "verify_time_s": time.perf_counter() - started,
        "ok": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and rebuild a CPSS dedup staging bundle.")
    parser.add_argument("--mode", choices=["bundle", "raw"], required=True)
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--expected-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result-json", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "bundle":
        if args.bundle_dir is None:
            raise SystemExit("--bundle-dir is required for bundle mode")
        result = rebuild_from_bundle(args.bundle_dir, args.output_dir)
    else:
        if args.raw_dir is None or args.expected_json is None:
            raise SystemExit("--raw-dir and --expected-json are required for raw mode")
        result = verify_raw_tree(args.raw_dir, args.expected_json)
    args.result_json.parent.mkdir(parents=True, exist_ok=True)
    args.result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

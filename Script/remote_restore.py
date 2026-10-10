#!/usr/bin/env python3
"""Restore one staged artifact on the Shanghai host and report process time."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import lz4.frame as lz4_frame
except ImportError:  # pragma: no cover
    lz4_frame = None

from Compressor.container import decode_metadata, parse_header
from Compressor.reversible import decompress_from_pipeline_id
from Compressor import FIXED_HEADER_SIZE


def restore(method: str, source: Path, destination: Path) -> tuple[float, int, str]:
    started = time.perf_counter()
    blob = source.read_bytes()
    if method == "raw":
        restored = blob
    elif method == "gzip":
        restored = gzip.decompress(blob)
    elif method == "lz4":
        if lz4_frame is None:
            raise RuntimeError("lz4.frame is unavailable")
        restored = lz4_frame.decompress(blob)
    elif method == "cpss":
        header = parse_header(blob[:FIXED_HEADER_SIZE])
        cursor = FIXED_HEADER_SIZE
        metadata_blob = blob[cursor:cursor + header.metadata_size]
        cursor += header.metadata_size
        tail = blob[cursor:cursor + header.tail_size]
        cursor += header.tail_size
        payload = blob[cursor:cursor + header.payload_size]
        metadata = decode_metadata(metadata_blob)
        restored = decompress_from_pipeline_id(
            payload,
            header.pipeline_id,
            metadata,
            tail,
        )
        if len(restored) != header.original_size:
            raise RuntimeError(
                f"CPSS restored size mismatch: expected {header.original_size}, got {len(restored)}"
            )
    else:
        raise ValueError(f"unsupported restore method: {method}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(restored)
    elapsed = time.perf_counter() - started
    digest = hashlib.sha256(restored).hexdigest()
    return elapsed, len(restored), digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("method", choices=["raw", "gzip", "lz4", "cpss"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    elapsed, size, digest = restore(args.method, args.source, args.destination)
    print(json.dumps({
        "method": args.method,
        "restore_time_sec": elapsed,
        "restored_size_bytes": size,
        "sha256": digest,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

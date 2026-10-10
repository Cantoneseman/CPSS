from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import FIXED_HEADER_SIZE
from .container import decode_metadata, parse_header
from .dedup import BlockRef, FileRecord, UniqueBlockRecord
from .reversible import decompress_from_pipeline_id


SCHEMA_VERSION = 1


def _block_to_dict(block: BlockRef) -> dict[str, Any]:
    return {
        "block_index": block.block_index,
        "offset": block.offset,
        "size_bytes": block.size_bytes,
        "block_hash": block.block_hash,
        "is_duplicate_block": block.is_duplicate_block,
        "unique_block_id": block.unique_block_id,
    }


def build_manifest(
    *,
    run_id: str,
    source_root: Path,
    block_size_bytes: int,
    files: list[FileRecord],
    unique_blocks: list[UniqueBlockRecord],
    stats: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "block_size_bytes": block_size_bytes,
        "source_root": str(source_root),
        "files": [
            {
                "file_id": item.file_id,
                "rel_path": item.rel_path,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "is_duplicate_file": item.is_duplicate_file,
                "duplicate_of_file_id": item.duplicate_of_file_id,
                "mode": item.mode,
                "mtime_ns": item.mtime_ns,
                "blocks": [_block_to_dict(block) for block in item.blocks],
            }
            for item in files
        ],
        "unique_blocks": [
            {
                "unique_block_id": item.unique_block_id,
                "block_hash": item.block_hash,
                "raw_size": item.raw_size,
                "source_file_id": item.source_file_id,
                "source_block_index": item.source_block_index,
                "raw_path": item.raw_path,
                "cpss_path": item.cpss_path,
                "compressed_size": item.compressed_size,
                "pipeline_name": item.pipeline_name,
                "pipeline_id": item.pipeline_id,
                "compress_time_s": item.compress_time_s,
                "cpss_sha256": item.cpss_sha256,
            }
            for item in unique_blocks
        ],
        "stats": dict(stats),
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> int:
    validate_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(encoded)
    return len(encoded)


def read_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def _safe_relative_path(value: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"unsafe relative path: {value!r}")


def validate_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported or missing manifest schema_version")
    if not manifest.get("run_id"):
        raise ValueError("manifest run_id is required")
    block_size = manifest.get("block_size_bytes")
    if not isinstance(block_size, int) or block_size <= 0:
        raise ValueError("manifest block_size_bytes must be a positive integer")
    files = manifest.get("files")
    unique_blocks = manifest.get("unique_blocks")
    if not isinstance(files, list) or not isinstance(unique_blocks, list):
        raise ValueError("manifest files and unique_blocks must be lists")

    file_ids: set[str] = set()
    file_paths: set[str] = set()
    block_ids: set[str] = set()
    block_hashes: dict[str, int] = {}
    for item in unique_blocks:
        if not isinstance(item, dict):
            raise ValueError("unique block entry must be an object")
        block_id = item.get("unique_block_id")
        block_hash = item.get("block_hash")
        raw_size = item.get("raw_size")
        cpss_path = item.get("cpss_path")
        if not isinstance(block_id, str) or block_id in block_ids:
            raise ValueError("duplicate or invalid unique_block_id")
        if not isinstance(block_hash, str) or len(block_hash) != 64:
            raise ValueError(f"invalid block hash for {block_id}")
        if not isinstance(raw_size, int) or raw_size <= 0:
            raise ValueError(f"invalid raw_size for {block_id}")
        if not isinstance(cpss_path, str):
            raise ValueError(f"missing cpss_path for {block_id}")
        _safe_relative_path(cpss_path)
        block_ids.add(block_id)
        block_hashes[block_hash] = raw_size

    for item in files:
        if not isinstance(item, dict):
            raise ValueError("file entry must be an object")
        file_id = item.get("file_id")
        rel_path = item.get("rel_path")
        size_bytes = item.get("size_bytes")
        file_hash = item.get("sha256")
        if not isinstance(file_id, str) or file_id in file_ids:
            raise ValueError("duplicate or invalid file_id")
        if not isinstance(rel_path, str) or rel_path in file_paths:
            raise ValueError("duplicate or invalid file path")
        _safe_relative_path(rel_path)
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise ValueError(f"invalid size_bytes for {file_id}")
        if not isinstance(file_hash, str) or len(file_hash) != 64:
            raise ValueError(f"invalid file hash for {file_id}")
        duplicate = bool(item.get("is_duplicate_file"))
        duplicate_of = item.get("duplicate_of_file_id")
        if duplicate and not isinstance(duplicate_of, str):
            raise ValueError(f"duplicate file {file_id} has no source")
        if not duplicate and duplicate_of is not None:
            raise ValueError(f"unique file {file_id} has duplicate source")
        blocks = item.get("blocks")
        if not isinstance(blocks, list):
            raise ValueError(f"blocks missing for {file_id}")
        total = 0
        expected_index = 0
        for block in blocks:
            if not isinstance(block, dict):
                raise ValueError("file block entry must be an object")
            if block.get("block_index") != expected_index:
                raise ValueError(f"non-contiguous block index for {file_id}")
            expected_index += 1
            offset = block.get("offset")
            block_size = block.get("size_bytes")
            block_hash = block.get("block_hash")
            block_id = block.get("unique_block_id")
            if not isinstance(offset, int) or offset != total:
                raise ValueError(f"invalid block offset for {file_id}")
            if not isinstance(block_size, int) or block_size <= 0:
                raise ValueError(f"invalid block size for {file_id}")
            if block_hash not in block_hashes or block_hashes[block_hash] != block_size:
                raise ValueError(f"block does not reference a unique block for {file_id}")
            if block_id not in block_ids:
                raise ValueError(f"unknown unique block id for {file_id}")
            total += block_size
        if total != size_bytes:
            raise ValueError(f"file {file_id} block total does not match size")
        file_ids.add(file_id)
        file_paths.add(rel_path)

    for item in files:
        source = item.get("duplicate_of_file_id")
        if source is not None and source not in file_ids:
            raise ValueError(f"unknown duplicate source {source}")


def decompress_cpss_blob(blob: bytes) -> bytes:
    header = parse_header(blob[:FIXED_HEADER_SIZE])
    cursor = FIXED_HEADER_SIZE
    metadata_blob = blob[cursor:cursor + header.metadata_size]
    cursor += header.metadata_size
    tail = blob[cursor:cursor + header.tail_size]
    cursor += header.tail_size
    payload = blob[cursor:cursor + header.payload_size]
    if len(payload) != header.payload_size or len(tail) != header.tail_size:
        raise ValueError("truncated CPSS blob")
    metadata = decode_metadata(metadata_blob)
    restored = decompress_from_pipeline_id(payload, header.pipeline_id, metadata, tail)
    if len(restored) != header.original_size:
        raise ValueError("CPSS restored size does not match header")
    return restored


def rebuild_from_bundle(
    bundle_root: Path,
    output_root: Path,
    *,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = manifest or read_manifest(bundle_root / "manifest.json")
    validate_manifest(manifest)
    block_cache: dict[str, bytes] = {}
    block_results: list[dict[str, Any]] = []
    started = time.perf_counter()
    decompress_started = time.perf_counter()
    for item in manifest["unique_blocks"]:
        cpss_path = bundle_root / item["cpss_path"]
        if not cpss_path.is_file():
            raise FileNotFoundError(cpss_path)
        cpss_hash = hashlib.sha256(cpss_path.read_bytes()).hexdigest()
        if item.get("cpss_sha256") and cpss_hash != item["cpss_sha256"]:
            raise ValueError(f"CPSS hash mismatch for {item['unique_block_id']}")
        raw = decompress_cpss_blob(cpss_path.read_bytes())
        if len(raw) != item["raw_size"] or hashlib.sha256(raw).hexdigest() != item["block_hash"]:
            raise ValueError(f"raw block verification failed for {item['unique_block_id']}")
        block_cache[item["unique_block_id"]] = raw
        block_results.append({
            "unique_block_id": item["unique_block_id"],
            "block_hash": item["block_hash"],
            "cpss_sha256": cpss_hash,
            "raw_size": len(raw),
            "ok": True,
        })
    decompress_time = time.perf_counter() - decompress_started

    rebuild_started = time.perf_counter()
    file_results: list[dict[str, Any]] = []
    for item in manifest["files"]:
        target = output_root / item["rel_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            for block in item["blocks"]:
                handle.write(block_cache[block["unique_block_id"]])
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        ok = target.stat().st_size == item["size_bytes"] and digest == item["sha256"]
        if item.get("mode") is not None:
            os.chmod(target, int(item["mode"]))
        if not ok:
            raise ValueError(f"reconstructed file verification failed: {item['rel_path']}")
        file_results.append({
            "file_id": item["file_id"],
            "rel_path": item["rel_path"],
            "size_bytes": target.stat().st_size,
            "sha256": digest,
            "ok": True,
        })
    rebuild_time = time.perf_counter() - rebuild_started
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": manifest["run_id"],
        "block_count": len(block_results),
        "file_count": len(file_results),
        "block_results": block_results,
        "file_results": file_results,
        "decompress_time_s": decompress_time,
        "rebuild_time_s": rebuild_time,
        "verify_time_s": time.perf_counter() - started,
        "ok": True,
    }

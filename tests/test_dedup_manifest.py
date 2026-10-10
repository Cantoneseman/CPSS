from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

from Compressor.container import build_header, encode_metadata
from Compressor.dedup import scan_directory
from Compressor.dedup_manifest import (
    build_manifest,
    rebuild_from_bundle,
    validate_manifest,
    write_manifest,
)
from Compressor.runtime import PipelineContext
from Compressor.selector import SELECTOR_MODE_CODES, exhaustive_select


def make_cpss(raw: bytes) -> bytes:
    context = PipelineContext(dtype_name=None, shape=(len(raw),), typesize=1)
    metadata = {
        "dtype_name": None,
        "shape": [len(raw)],
        "typesize": 1,
        "endian": "little",
        "aligned_size": len(raw),
        "element_count": len(raw),
        "scenario": "science",
        "format": "bin",
    }
    selection = exhaustive_select(raw, context, metadata, ["zstd", "zlib", "lz4"])
    metadata_blob = encode_metadata(selection.artifact.metadata)
    header = build_header(
        selector_mode=SELECTOR_MODE_CODES[selection.selector_mode],
        pipeline_id=selection.artifact.pipeline_id,
        metadata_size=len(metadata_blob),
        tail_size=len(selection.artifact.tail),
        payload_size=len(selection.artifact.payload),
        original_size=len(raw),
    )
    return header + metadata_blob + selection.artifact.tail + selection.artifact.payload


def test_manifest_roundtrip_and_rebuild(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.bin").write_bytes(b"A" * 8 + b"B" * 3)
    (source / "empty.txt").write_bytes(b"")
    files, unique_blocks, stats = scan_directory(source, 8)

    bundle = tmp_path / "bundle"
    (bundle / "blocks").mkdir(parents=True)
    for block in unique_blocks:
        raw = (source / next(item.rel_path for item in files if item.file_id == block.source_file_id)).read_bytes()
        start = files[[item.file_id for item in files].index(block.source_file_id)].blocks[block.source_block_index].offset
        payload = raw[start:start + block.raw_size]
        cpss_path = bundle / "blocks" / f"{block.block_hash}.cpss"
        cpss_path.write_bytes(make_cpss(payload))
        block.cpss_path = f"blocks/{cpss_path.name}"
        block.compressed_size = cpss_path.stat().st_size
        block.cpss_sha256 = hashlib.sha256(cpss_path.read_bytes()).hexdigest()

    manifest = build_manifest(
        run_id="test-run",
        source_root=source,
        block_size_bytes=8,
        files=files,
        unique_blocks=unique_blocks,
        stats=stats.as_dict(),
        created_at="2026-07-28T00:00:00Z",
    )
    manifest_path = bundle / "manifest.json"
    write_manifest(manifest_path, manifest)
    output = tmp_path / "output"
    result = rebuild_from_bundle(bundle, output)

    assert result["ok"] is True
    assert (output / "a.bin").read_bytes() == (source / "a.bin").read_bytes()
    assert (output / "empty.txt").read_bytes() == b""


def test_manifest_rejects_unsafe_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.bin").write_bytes(b"data")
    files, blocks, stats = scan_directory(source, 8)
    manifest = build_manifest(
        run_id="unsafe",
        source_root=source,
        block_size_bytes=8,
        files=files,
        unique_blocks=[
            type(block)(
                **{
                    **block.__dict__,
                    "cpss_path": f"blocks/{block.block_hash}.cpss",
                }
            )
            for block in blocks
        ],
        stats=stats.as_dict(),
        created_at="now",
    )
    broken = copy.deepcopy(manifest)
    broken["files"][0]["rel_path"] = "../escape"
    with pytest.raises(ValueError, match="unsafe"):
        validate_manifest(broken)


def test_manifest_rejects_missing_block_reference(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.bin").write_bytes(b"data")
    files, blocks, stats = scan_directory(source, 8)
    manifest = build_manifest(
        run_id="missing",
        source_root=source,
        block_size_bytes=8,
        files=files,
        unique_blocks=[
            type(block)(
                **{
                    **block.__dict__,
                    "cpss_path": f"blocks/{block.block_hash}.cpss",
                }
            )
            for block in blocks
        ],
        stats=stats.as_dict(),
        created_at="now",
    )
    broken = copy.deepcopy(manifest)
    broken["files"][0]["blocks"][0]["unique_block_id"] = "block_missing"
    with pytest.raises(ValueError, match="unknown unique block"):
        validate_manifest(broken)

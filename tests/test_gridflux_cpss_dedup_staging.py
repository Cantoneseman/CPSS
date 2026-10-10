from __future__ import annotations

import argparse
import json
from pathlib import Path

from Compressor.dedup import scan_directory
from Script.gridflux_cpss_dedup_staging import (
    make_cpss_only_records,
    serialize_expected_files,
)


def test_expected_file_manifest_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "b.bin").write_bytes(b"b")
    (tmp_path / "a.bin").write_bytes(b"a")
    output = tmp_path / "expected.json"

    rows = serialize_expected_files(tmp_path, output)

    assert [row["rel_path"] for row in rows] == ["a.bin", "b.bin"]
    assert json.loads(output.read_text(encoding="utf-8")) == rows


def test_cpss_only_records_do_not_deduplicate(tmp_path: Path) -> None:
    payload = b"A" * 8 + b"B" * 8
    (tmp_path / "a.bin").write_bytes(payload)
    (tmp_path / "b.bin").write_bytes(payload)

    files, blocks, stats = make_cpss_only_records(tmp_path, 8)

    assert len(files) == 2
    assert len(blocks) == 4
    assert stats["block_duplicate_count"] == 0
    assert stats["block_unique_count"] == 4


def test_scan_smoke_has_expected_dedup_counts(tmp_path: Path) -> None:
    block_a = b"A" * 8
    block_b = b"B" * 8
    block_c = b"C" * 8
    (tmp_path / "file_a.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_b.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_c.bin").write_bytes(block_a + block_c)
    (tmp_path / "file_empty.txt").write_bytes(b"")

    _files, blocks, stats = scan_directory(tmp_path, 8)

    assert len(blocks) == 3
    assert stats.file_count == 4
    assert stats.duplicate_file_count == 1
    assert stats.block_duplicate_count == 1

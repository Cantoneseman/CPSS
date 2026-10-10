from __future__ import annotations

from pathlib import Path

import pytest

from Compressor.dedup import iter_blocks, scan_directory, sha256_bytes, sha256_file


@pytest.mark.parametrize("block_size", [1, 4, 8, 16, 32])
def test_fixed_blocks_cover_file_without_gaps(tmp_path: Path, block_size: int) -> None:
    payload = bytes(range(64))
    source = tmp_path / "data.bin"
    source.write_bytes(payload)

    blocks = list(iter_blocks(source, block_size))

    assert b"".join(chunk for _offset, chunk in blocks) == payload
    assert [offset for offset, _chunk in blocks] == list(range(0, len(payload), block_size))
    assert all(len(chunk) <= block_size for _offset, chunk in blocks)


def test_hash_helpers_match_hashlib(tmp_path: Path) -> None:
    source = tmp_path / "data.bin"
    source.write_bytes(b"abc" * 100)

    assert sha256_file(source) == sha256_bytes(source.read_bytes())


def test_empty_file_has_no_blocks(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_bytes(b"")

    files, blocks, stats = scan_directory(tmp_path, 8)

    assert len(files) == 1
    assert files[0].blocks == []
    assert blocks == []
    assert stats.raw_bytes == 0
    assert stats.unique_raw_bytes == 0


def test_file_and_block_dedup_are_distinct(tmp_path: Path) -> None:
    block_a = b"A" * 8
    block_b = b"B" * 8
    block_c = b"C" * 8
    (tmp_path / "file_a.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_b.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_c.bin").write_bytes(block_a + block_c)

    files, blocks, stats = scan_directory(tmp_path, 8)
    by_name = {item.rel_path: item for item in files}

    assert by_name["file_b.bin"].is_duplicate_file is True
    assert by_name["file_b.bin"].duplicate_of_file_id == by_name["file_a.bin"].file_id
    assert by_name["file_c.bin"].is_duplicate_file is False
    assert by_name["file_c.bin"].blocks[0].is_duplicate_block is True
    assert by_name["file_c.bin"].blocks[1].is_duplicate_block is False
    assert len(blocks) == 3
    assert stats.duplicate_file_count == 1
    assert stats.block_duplicate_count == 1
    assert stats.file_dedup_saved_bytes == 16
    assert stats.block_dedup_saved_bytes == 8
    assert stats.unique_raw_bytes == 24


def test_short_and_exact_block_sizes(tmp_path: Path) -> None:
    (tmp_path / "short.bin").write_bytes(b"x" * 7)
    (tmp_path / "exact.bin").write_bytes(b"y" * 8)

    files, _blocks, stats = scan_directory(tmp_path, 8)

    assert sorted(item.size_bytes for item in files) == [7, 8]
    assert stats.block_total_count == 2
    assert stats.unique_raw_bytes == 15


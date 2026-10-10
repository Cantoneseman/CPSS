from __future__ import annotations

from pathlib import Path

import pytest

from Script.range_downloader import DownloadPlan, assemble_from_ranges, split_existing_parts


def test_assemble_from_ranges_downloads_missing_parts_and_reuses_complete_parts(tmp_path: Path) -> None:
    payload = b"abcdefghijklmnopqrstuvwxyz0123456789"
    out = tmp_path / "sample.bin"
    part_dir = tmp_path / ".sample.bin.parts"
    part_dir.mkdir()
    (part_dir / "part_000000").write_bytes(payload[:10])
    fetched: list[tuple[int, int, Path]] = []

    def fetcher(start: int, end: int, part_path: Path) -> None:
        fetched.append((start, end, part_path))
        part_path.write_bytes(payload[start : end + 1])

    plan = DownloadPlan(url="https://example.invalid/sample.bin", output_path=out, expected_bytes=len(payload), chunk_bytes=10)

    result = assemble_from_ranges(plan, fetcher=fetcher, part_dir=part_dir)

    assert result.status == "downloaded"
    assert result.bytes_written == len(payload)
    assert out.read_bytes() == payload
    assert fetched == [
        (10, 19, part_dir / "part_000001"),
        (20, 29, part_dir / "part_000002"),
        (30, 35, part_dir / "part_000003"),
    ]
    assert not part_dir.exists()


def test_assemble_from_ranges_rejects_wrong_sized_fetched_part(tmp_path: Path) -> None:
    out = tmp_path / "sample.bin"

    def bad_fetcher(start: int, end: int, part_path: Path) -> None:
        part_path.write_bytes(b"too-short")

    plan = DownloadPlan(url="https://example.invalid/sample.bin", output_path=out, expected_bytes=64, chunk_bytes=32)

    with pytest.raises(RuntimeError, match="wrong size"):
        assemble_from_ranges(plan, fetcher=bad_fetcher)

    assert not out.exists()


def test_split_existing_parts_reuses_full_subparts_and_discards_remainder(tmp_path: Path) -> None:
    part_dir = tmp_path / ".sample.bin.parts"
    part_dir.mkdir()
    (part_dir / "part_000000").write_bytes(b"abcdefghijkl")
    (part_dir / "part_000001").write_bytes(b"mnopq")

    converted = split_existing_parts(part_dir, source_chunk_bytes=12, target_chunk_bytes=3, expected_bytes=24)

    assert converted == 5
    assert (part_dir / "part_000000").read_bytes() == b"abc"
    assert (part_dir / "part_000001").read_bytes() == b"def"
    assert (part_dir / "part_000002").read_bytes() == b"ghi"
    assert (part_dir / "part_000003").read_bytes() == b"jkl"
    assert (part_dir / "part_000004").read_bytes() == b"mno"
    assert not (part_dir / "part_000005").exists()

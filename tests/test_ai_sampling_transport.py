from __future__ import annotations

from pathlib import Path

from Script.run_ai_sampling_transport_experiment import (
    allocate_file_budgets,
    sample_offsets,
)


def test_head_sampling_is_sequential(tmp_path: Path) -> None:
    source = tmp_path / "data.bin"
    source.write_bytes(bytes(range(256)) * 1024)
    assert sample_offsets(source, "head_sequential", 3, seed=20260727, chunk_bytes=256) == [0, 256, 512]


def test_random_sampling_is_deterministic_and_spread(tmp_path: Path) -> None:
    source = tmp_path / "data.bin"
    source.write_bytes(bytes(range(256)) * 4096)
    first = sample_offsets(source, "uniform_random", 8, seed=20260727, chunk_bytes=256)
    second = sample_offsets(source, "uniform_random", 8, seed=20260727, chunk_bytes=256)
    assert first == second
    assert len(set(first)) == len(first)
    assert max(first) > 0


def test_stratified_sampling_covers_file_positions(tmp_path: Path) -> None:
    source = tmp_path / "data.bin"
    source.write_bytes(bytes(range(256)) * 4096)
    offsets = sample_offsets(source, "position_stratified", 5, seed=20260727, chunk_bytes=256)
    assert offsets[0] == 0
    assert offsets[-1] > offsets[0]
    assert offsets == sorted(offsets)


def test_budget_allocation_is_round_robin() -> None:
    files = [(Path("a"), "a"), (Path("b"), "b"), (Path("c"), "c")]
    allocation = allocate_file_budgets(files, 8)
    assert [allocation[path] for path, _ in files] == [3, 3, 2]

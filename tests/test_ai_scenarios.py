from __future__ import annotations

from pathlib import Path

from Compressor.feature_extraction import (
    SCENARIO_FEATURE_NAMES,
    extract_scenario_features_from_sample,
)
from Script.run_ai_scenarios import assign_internal_split, iter_chunks


def test_text_chunks_keep_line_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "records.jsonl"
    source.write_bytes(b"one\n" + b"two\n" + b"three\n" + b"four\n")

    chunks = list(iter_chunks(source, 8))

    assert b"".join(payload for _offset, payload in chunks) == source.read_bytes()
    assert all(payload.endswith(b"\n") for _offset, payload in chunks[:-1])


def test_binary_chunks_cover_offsets(tmp_path: Path) -> None:
    source = tmp_path / "data.parquet"
    source.write_bytes(bytes(range(32)))

    chunks = list(iter_chunks(source, 10))

    assert [offset for offset, _payload in chunks] == [0, 10, 20, 30]
    assert b"".join(payload for _offset, payload in chunks) == source.read_bytes()


def test_internal_split_is_group_local_and_deterministic() -> None:
    rows = [
        {"id": str(index), "source_group": group, "split": "external"}
        for index, group in enumerate(["a"] * 5 + ["b"] * 5)
    ]

    assign_internal_split(rows, 20260727)
    first = [(row["id"], row["split"]) for row in rows]
    assign_internal_split(rows, 20260727)
    second = [(row["id"], row["split"]) for row in rows]

    assert first == second
    assert all(
        sum(row["split"] == "test" for row in rows if row["source_group"] == group) == 1
        for group in ("a", "b")
    )


def test_scenario_features_are_bounded_and_named() -> None:
    values = extract_scenario_features_from_sample(
        b'{"text":"hello"}\n' * 32,
        512,
        original_suffix=".jsonl",
        scenario="llm_text",
        metadata={"format": "jsonl"},
    )

    assert len(values) == len(SCENARIO_FEATURE_NAMES)
    assert values.min() >= 0.0
    assert values.max() <= 1.0

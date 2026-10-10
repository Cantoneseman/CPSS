from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

import Compressor.cpss_sampling_matrix as cpss_matrix
from Compressor.decision_memory import DecisionKey, DecisionMemory
from Compressor.decision_features import decision_feature_bucket, extract_decision_features
from Compressor.dedup import UniqueBlockRecord
from Compressor.dedup_manifest import decompress_cpss_blob
from Compressor.cpss_sampling_matrix import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLING_MODE,
    calibrate_fixed_pipeline,
    choose_pipeline_from_trials,
    compress_records_to_bundle,
    fixed_sample_indices,
    make_no_dedup_records,
    make_native_exhaustive_block_compressor,
    random_sample_indices,
    scan_dedup_records,
    select_default_calibration,
    system_cost_seconds,
)


def test_fixed_sampling_indices_are_position_stratified() -> None:
    assert fixed_sample_indices(1) == [0]
    assert fixed_sample_indices(10) == [0, 2, 4, 7, 9]
    assert fixed_sample_indices(3) == [0, 1, 2]


def test_random_sampling_indices_are_seed_reproducible() -> None:
    first = random_sample_indices(100, seed=20260727, count=5)
    second = random_sample_indices(100, seed=20260727, count=5)
    different = random_sample_indices(100, seed=20260728, count=5)

    assert first == second
    assert first != different
    assert first == sorted(first)


def test_random_sampling_is_default_calibration_mode() -> None:
    fixed = {"sampling_mode": "fixed_sampling", "selected_pipeline": "fixed"}
    random_calibrations = [{"sampling_mode": "random_sampling", "selected_pipeline": "random"}]

    assert DEFAULT_SAMPLING_MODE == "random_sampling"
    assert DEFAULT_RANDOM_SEED == 20260727
    assert select_default_calibration(fixed, random_calibrations) is random_calibrations[0]


def test_calibration_tie_breaks_by_time_then_name() -> None:
    trials = [
        {"sample_block_index": 0, "pipeline_name": "zstd", "compressed_size": 10, "compress_time_s": 2.0, "status": "ok"},
        {"sample_block_index": 1, "pipeline_name": "zstd", "compressed_size": 10, "compress_time_s": 2.0, "status": "ok"},
        {"sample_block_index": 0, "pipeline_name": "lz4", "compressed_size": 10, "compress_time_s": 1.0, "status": "ok"},
        {"sample_block_index": 1, "pipeline_name": "lz4", "compressed_size": 10, "compress_time_s": 1.0, "status": "ok"},
        {"sample_block_index": 0, "pipeline_name": "bzip2", "compressed_size": 9, "compress_time_s": 9.0, "status": "failed"},
        {"sample_block_index": 1, "pipeline_name": "bzip2", "compressed_size": 9, "compress_time_s": 9.0, "status": "ok"},
    ]

    selected = choose_pipeline_from_trials(trials)

    assert selected["pipeline_name"] == "lz4"


def test_calibration_selects_minimum_system_cost_not_minimum_bytes() -> None:
    trials = [
        {
            "sample_block_index": 0,
            "pipeline_name": "small_slow",
            "compressed_size": 10,
            "compress_time_s": 10.0,
            "decompress_time_s": 1.0,
            "status": "ok",
        },
        {
            "sample_block_index": 1,
            "pipeline_name": "small_slow",
            "compressed_size": 10,
            "compress_time_s": 10.0,
            "decompress_time_s": 1.0,
            "status": "ok",
        },
        {
            "sample_block_index": 0,
            "pipeline_name": "large_fast",
            "compressed_size": 20,
            "compress_time_s": 0.1,
            "decompress_time_s": 0.1,
            "status": "ok",
        },
        {
            "sample_block_index": 1,
            "pipeline_name": "large_fast",
            "compressed_size": 20,
            "compress_time_s": 0.1,
            "decompress_time_s": 0.1,
            "status": "ok",
        },
    ]

    selected = choose_pipeline_from_trials(trials, bandwidth_Bps=10.0)

    assert selected["pipeline_name"] == "large_fast"
    assert selected["estimated_system_cost_s"] == system_cost_seconds(0.2, 40, 0.2, 10.0)


def test_system_cost_rejects_missing_or_nonpositive_bandwidth() -> None:
    with pytest.raises(ValueError):
        system_cost_seconds(1.0, 10, 1.0, None)
    with pytest.raises(ValueError):
        system_cost_seconds(1.0, 10, 1.0, 0.0)


def test_native_exhaustive_reuses_exact_persisted_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = bytes(range(256)) * 16
    block = UniqueBlockRecord(
        unique_block_id="block_1",
        block_hash=hashlib.sha256(raw).hexdigest(),
        raw_size=len(raw),
        source_file_id="file_1",
        source_block_index=0,
    )
    memory_path = tmp_path / "decision-memory.sqlite3"
    memory = DecisionMemory(memory_path)
    actual_compress = cpss_matrix.compress_fixed_pipeline_blob
    calls: list[str] = []

    def count_compress(raw_bytes: bytes, pipeline_name: str, **kwargs: object) -> tuple[bytes, dict[str, object]]:
        calls.append(pipeline_name)
        return actual_compress(raw_bytes, pipeline_name, **kwargs)

    monkeypatch.setattr(cpss_matrix, "compress_fixed_pipeline_blob", count_compress)
    compressor = make_native_exhaustive_block_compressor(
        ["zstd", "bzip2"],
        decision_memory=memory,
    )

    cold_blob, cold_meta = compressor(raw, block)
    assert cold_meta["decision_source"] == "exhaustive"
    assert cold_meta["candidate_calls"] == 2
    assert len(calls) == 2
    memory.close()

    calls.clear()
    memory = DecisionMemory(memory_path)
    compressor = make_native_exhaustive_block_compressor(
        ["zstd", "bzip2"],
        decision_memory=memory,
    )
    hot_blob, hot_meta = compressor(raw, block)
    assert hot_meta["decision_source"] == "exact_memory"
    assert hot_meta["candidate_calls"] == 1
    assert hot_meta["pipeline_name"] == cold_meta["pipeline_name"]
    assert len(calls) == 1
    assert decompress_cpss_blob(hot_blob) == raw
    assert decompress_cpss_blob(cold_blob) == raw
    memory.close()


def test_calibration_reuses_persisted_block_trials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = bytes(range(256)) * 16
    source = tmp_path / "source"
    source.mkdir()
    (source / "block.bin").write_bytes(raw)
    files, blocks, _stats = make_no_dedup_records(source, len(raw))
    actual_compress = cpss_matrix.compress_fixed_pipeline_blob
    calls: list[str] = []

    def count_compress(raw_bytes: bytes, pipeline_name: str, **kwargs: object) -> tuple[bytes, dict[str, object]]:
        calls.append(pipeline_name)
        return actual_compress(raw_bytes, pipeline_name, **kwargs)

    monkeypatch.setattr(cpss_matrix, "compress_fixed_pipeline_blob", count_compress)
    memory_path = tmp_path / "calibration-memory.sqlite3"
    memory = DecisionMemory(memory_path)
    cold = calibrate_fixed_pipeline(
        source,
        files,
        blocks,
        sampling_mode="random_sampling",
        candidates=["zstd", "bzip2"],
        sample_count=1,
        seed=42,
        bandwidth_Bps=12_500_000.0,
        decision_memory=memory,
    )
    assert cold["decision_memory_misses"] == 1
    assert cold["decision_memory_hits"] == 0
    assert cold["candidate_pipeline_calls"] == 2
    assert len(calls) == 2
    memory.close()

    calls.clear()
    memory = DecisionMemory(memory_path)
    warm = calibrate_fixed_pipeline(
        source,
        files,
        blocks,
        sampling_mode="random_sampling",
        candidates=["zstd", "bzip2"],
        sample_count=1,
        seed=42,
        bandwidth_Bps=12_500_000.0,
        decision_memory=memory,
    )
    assert warm["decision_memory_hits"] == 1
    assert warm["decision_memory_misses"] == 0
    assert warm["candidate_pipeline_calls"] == 0
    assert calls == []
    assert warm["selected_pipeline"] == cold["selected_pipeline"]
    assert [row["compressed_size"] for row in warm["trials"]] == [row["compressed_size"] for row in cold["trials"]]
    memory.close()


def test_native_exhaustive_uses_consensus_for_similar_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"similar NOAA csv row\n" * 256
    block = UniqueBlockRecord(
        unique_block_id="held-out",
        block_hash=hashlib.sha256(raw).hexdigest(),
        raw_size=len(raw),
        source_file_id="file_1",
        source_block_index=0,
    )
    memory = DecisionMemory(tmp_path / "similar-memory.sqlite3")
    candidates = ("zstd", "bzip2")
    runtime_signature = cpss_matrix._decision_memory_runtime_signature()
    context = {
        "dtype_name": None,
        "full_shape": None,
        "endian": "little",
        "block_offset": 0,
        "scenario": "science",
        "source_group": "cpss_sampling_dedup_matrix",
        "bandwidth_Bps": None,
        "receiver_decompress_profile": {},
    }
    features = {"entropy_bits": 4.0, "printable_fraction": 0.94, "zero_fraction": 0.01,
                "same_adjacent_fraction": 0.08, "gzip_ratio": 3.2}
    bucket = decision_feature_bucket(features)
    ranking = [
        {"pipeline_name": "zstd", "status": "ok", "compressed_size": 100, "compress_time_s": 0.1,
         "decompress_time_s": 0.01, "estimated_system_cost_s": ""},
        {"pipeline_name": "bzip2", "status": "ok", "compressed_size": 140, "compress_time_s": 0.2,
         "decompress_time_s": 0.02, "estimated_system_cost_s": ""},
    ]
    for index in range(3):
        neighbor_key = DecisionKey(
            block_hash=f"{index + 101:064x}", raw_size=len(raw), context=context,
            candidates=candidates, objective="stored_size", runtime_signature=runtime_signature,
        )
        memory.record(neighbor_key, selected_pipeline="zstd", ranking=ranking,
                      similarity_bucket=bucket, similarity_features=features)
    monkeypatch.setattr(cpss_matrix, "extract_decision_features", lambda _raw: features)
    monkeypatch.setattr(cpss_matrix, "decision_feature_bucket", lambda _features: bucket)
    actual_compress = cpss_matrix.compress_fixed_pipeline_blob
    calls: list[str] = []

    def count_compress(raw_bytes: bytes, pipeline_name: str, **kwargs: object) -> tuple[bytes, dict[str, object]]:
        calls.append(pipeline_name)
        return actual_compress(raw_bytes, pipeline_name, **kwargs)

    monkeypatch.setattr(cpss_matrix, "compress_fixed_pipeline_blob", count_compress)
    compressor = make_native_exhaustive_block_compressor(candidates, decision_memory=memory)
    blob, meta = compressor(raw, block)
    assert meta["decision_source"] == "similar_memory"
    assert meta["candidate_calls"] == 1
    assert meta["similar_memory_neighbors"] == 3
    assert meta["similar_memory_agreement"] == 1.0
    assert calls == ["zstd"]
    assert decompress_cpss_blob(blob) == raw
    memory.close()

def test_no_dedup_and_dedup_compress_expected_block_counts(tmp_path: Path) -> None:
    block_a = b"A" * 8
    block_b = b"B" * 8
    block_c = b"C" * 8
    (tmp_path / "file_a.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_b.bin").write_bytes(block_a + block_b)
    (tmp_path / "file_c.bin").write_bytes(block_a + block_c)
    (tmp_path / "file_empty.txt").write_bytes(b"")
    no_files, no_blocks, no_stats = make_no_dedup_records(tmp_path, 8)
    dedup_files, dedup_blocks, dedup_stats = scan_dedup_records(tmp_path, 8)
    calls: list[bytes] = []

    def fake_compressor(raw: bytes, _block: object) -> tuple[bytes, dict[str, object]]:
        calls.append(raw)
        return b"fake-cpss-" + raw[:1], {
            "pipeline_name": "zstd",
            "pipeline_id": 27,
            "selector_mode": "calibrated_fixed",
        }

    compress_records_to_bundle(
        root=tmp_path,
        bundle=tmp_path / "no_bundle",
        run_id="test-no",
        block_size_bytes=8,
        files=no_files,
        unique_blocks=no_blocks,
        stats=no_stats,
        dedup_enabled=False,
        compression_strategy="fixed_sampling",
        block_compressor=fake_compressor,
        created_at="now",
    )
    assert len(calls) == 6

    calls.clear()
    compress_records_to_bundle(
        root=tmp_path,
        bundle=tmp_path / "dedup_bundle",
        run_id="test-dedup",
        block_size_bytes=8,
        files=dedup_files,
        unique_blocks=dedup_blocks,
        stats=dedup_stats,
        dedup_enabled=True,
        compression_strategy="fixed_sampling",
        block_compressor=fake_compressor,
        created_at="now",
    )
    assert len(calls) == 3

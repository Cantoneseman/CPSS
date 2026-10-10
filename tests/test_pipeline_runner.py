from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np

from Compressor.pipeline_runner import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLING_MODE,
    PipelineRunConfig,
    run_pipeline,
)
from Compressor.reversible import compress_with_pipeline
from Compressor.runtime import build_context_for_block
from Compressor.selector import SelectionResult


def make_smoke_tree(root: Path, block_size: int = 4096) -> None:
    block_a = b"A" * block_size
    block_b = b"B" * block_size
    block_c = b"C" * block_size
    root.mkdir(parents=True, exist_ok=True)
    (root / "file_a.bin").write_bytes(block_a + block_b)
    (root / "file_b.bin").write_bytes(block_a + block_b)
    (root / "file_c.bin").write_bytes(block_a + block_c)
    (root / "file_empty.txt").write_bytes(b"")


def read_single_row(path: Path) -> dict[str, str]:
    rows = list(csv.DictReader(path.open("r", encoding="utf-8")))
    assert len(rows) == 1
    return rows[0]


def test_raw_strategy_writes_unified_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_smoke_tree(source)

    result = run_pipeline(PipelineRunConfig(
        input_dir=source,
        result_dir=tmp_path / "result_raw",
        strategy="raw",
        dedup=True,
    ))

    row = read_single_row(result.method_metrics_path)
    assert result.bundle_manifest_path is None
    assert result.source_manifest_path.exists()
    assert result.summary_path.exists()
    assert row["strategy"] == "raw"
    assert row["dedup"] == "off"
    assert int(row["transferred_bytes"]) == int(row["raw_bytes"])
    assert row["roundtrip_ok"] == "True"
    assert float(row["transfer_goodput_Bps"]) > 0
    assert float(row["e2e_goodput_Bps"]) > 0
    assert float(row["byte_reduction_ratio"]) == 1.0
    assert row["deprecated_logical_bandwidth_gain_vs_raw_bytes"] == row["logical_bandwidth_gain_vs_raw_bytes"]
    assert row["deprecated_e2e_excluding_calibration_s"] == row["warm_e2e_wall_clock_s"]


def test_random_sampling_dedup_reuses_unique_blocks_and_rebuilds(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_smoke_tree(source)

    no_dedup = run_pipeline(PipelineRunConfig(
        input_dir=source,
        result_dir=tmp_path / "result_no_dedup",
        strategy=DEFAULT_SAMPLING_MODE,
        dedup=False,
        block_size_bytes=4096,
        pipelines=["zstd"],
        sample_count=2,
        random_seed=DEFAULT_RANDOM_SEED,
    ))
    dedup = run_pipeline(PipelineRunConfig(
        input_dir=source,
        result_dir=tmp_path / "result_dedup",
        strategy=DEFAULT_SAMPLING_MODE,
        dedup=True,
        block_size_bytes=4096,
        pipelines=["zstd"],
        sample_count=2,
        random_seed=DEFAULT_RANDOM_SEED,
    ))

    no_row = no_dedup.method_metrics or {}
    dedup_row = dedup.method_metrics or {}
    assert no_dedup.bundle_manifest_path and no_dedup.bundle_manifest_path.exists()
    assert dedup.bundle_manifest_path and dedup.bundle_manifest_path.exists()
    assert no_dedup.rebuild_result and no_dedup.rebuild_result["ok"] is True
    assert dedup.rebuild_result and dedup.rebuild_result["ok"] is True
    assert int(no_row["block_unique_count"]) == 6
    assert int(dedup_row["block_unique_count"]) == 3
    assert int(dedup_row["duplicate_file_count"]) == 1
    assert int(dedup_row["block_duplicate_count"]) == 1
    assert int(dedup_row["transferred_bytes"]) < int(no_row["transferred_bytes"])
    assert dedup_row["selected_pipeline"] == "zstd"
    assert not (dedup.result_dir / "rebuild").exists()


def test_sampling_records_oracle_regret_and_wall_clock_metrics(tmp_path: Path) -> None:
    source = tmp_path / "source"
    make_smoke_tree(source, block_size=2048)

    result = run_pipeline(PipelineRunConfig(
        input_dir=source,
        result_dir=tmp_path / "result_metrics",
        strategy=DEFAULT_SAMPLING_MODE,
        dedup=False,
        block_size_bytes=2048,
        pipelines=["zstd"],
        sample_count=2,
        bandwidth_Bps=50_000.0,
        min_expected_speedup=1.0,
    ))

    row = read_single_row(result.method_metrics_path)
    assert row["selected_action"] == "compress"
    assert row["full_data_global_oracle_pipeline"] == "zstd"
    assert row["system_regret_vs_full_data_global_oracle"] != ""
    assert row["unsampled_block_system_regret"] != ""
    assert float(row["transfer_wall_clock_s"]) > 0
    assert float(row["output_ready_wall_clock_s"]) > 0
    assert float(row["verification_done_wall_clock_s"]) >= float(row["output_ready_wall_clock_s"])
    assert float(row["cold_e2e_wall_clock_s"]) >= float(row["warm_e2e_wall_clock_s"])
    assert float(row["byte_reduction_ratio"]) > 1.0
    assert (result.result_dir / "full_data_global_oracle.json").exists()
    cal_rows = list(csv.DictReader((result.result_dir / "calibration.csv").open("r", encoding="utf-8")))
    assert cal_rows
    assert all(row["decompress_time_s"] != "" for row in cal_rows)
    assert all(row["estimated_system_cost_s"] != "" for row in cal_rows)


def test_sampling_raw_bypass_for_high_entropy_and_compress_for_numeric(tmp_path: Path) -> None:
    high_entropy = tmp_path / "high_entropy"
    high_entropy.mkdir()
    (high_entropy / "noise.bin").write_bytes(bytes(range(256)) * 64)

    bypass = run_pipeline(PipelineRunConfig(
        input_dir=high_entropy,
        result_dir=tmp_path / "result_bypass",
        strategy=DEFAULT_SAMPLING_MODE,
        dedup=False,
        block_size_bytes=4096,
        pipelines=["zstd"],
        sample_count=1,
        bandwidth_Bps=1_000_000_000.0,
        min_expected_speedup=1.0,
    ))
    bypass_row = bypass.method_metrics or {}
    assert bypass_row["selected_action"] == "raw_bypass"
    assert bypass_row["bypass_reason"] == "predicted_compressed_e2e_not_better_than_raw_guard"
    assert bypass.bundle_manifest_path is None
    assert not (bypass.result_dir / "bundle").exists()
    assert float(bypass_row["fallback_overhead_s"]) > 0

    numeric = tmp_path / "numeric"
    numeric.mkdir()
    values = np.zeros(16_384, dtype="<f4")
    (numeric / "field.f32").write_bytes(values.tobytes())
    compressed = run_pipeline(PipelineRunConfig(
        input_dir=numeric,
        result_dir=tmp_path / "result_compress",
        strategy=DEFAULT_SAMPLING_MODE,
        dedup=False,
        block_size_bytes=16_384,
        pipelines=["zstd"],
        sample_count=1,
        bandwidth_Bps=50_000.0,
        dtype_name="float32",
        shape=values.shape,
        endian="little",
        min_expected_speedup=1.0,
    ))
    compressed_row = compressed.method_metrics or {}
    assert compressed_row["selected_action"] == "compress"
    assert compressed.bundle_manifest_path and compressed.bundle_manifest_path.exists()


def test_runner_writes_typed_context_into_cpss_metadata(tmp_path: Path) -> None:
    source = tmp_path / "typed"
    source.mkdir()
    values = np.arange(32, dtype="<f4").reshape(8, 4)
    (source / "field.f32").write_bytes(values.tobytes() + b"XY")

    result = run_pipeline(PipelineRunConfig(
        input_dir=source,
        result_dir=tmp_path / "result_typed",
        strategy="fixed",
        dedup=False,
        block_size_bytes=64,
        fixed_pipeline="shuffle->bzip2",
        dtype_name="float32",
        shape=values.shape,
        endian="little",
    ))

    assert result.bundle_manifest_path and result.bundle_manifest_path.exists()
    manifest = json.loads(result.bundle_manifest_path.read_text(encoding="utf-8"))
    assert manifest["stats"]["data_metadata"]["dtype_name"] == "float32"
    first_cpss = next((result.bundle_dir / item["cpss_path"]) for item in manifest["unique_blocks"])
    from Compressor.container import decode_metadata, parse_header
    from Compressor import FIXED_HEADER_SIZE

    blob = first_cpss.read_bytes()
    header = parse_header(blob[:FIXED_HEADER_SIZE])
    metadata = decode_metadata(blob[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + header.metadata_size])
    assert metadata["context"]["dtype_name"] == "float32"
    assert metadata["context"]["typesize"] == 4
    assert metadata["context"]["endian"] == "little"
    assert metadata["context"]["shape"] == [4, 4]


def test_selector_strategies_are_dispatched_without_loading_real_models(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "source"
    make_smoke_tree(source, block_size=1024)
    calls: list[str] = []

    def fake_select_pipeline(
        raw: bytes,
        context: Any,
        context_metadata: dict[str, Any],
        *,
        mode: str,
        model_dir: Path | None,
        top_k: int,
        candidates: list[str] | None,
    ) -> SelectionResult:
        calls.append(mode)
        artifact = compress_with_pipeline(
            raw,
            "zstd",
            context,
            context_metadata=context_metadata,
            selector_metadata={"mode": mode, "top_k": top_k, "model_dir": str(model_dir) if model_dir else ""},
        )
        return SelectionResult(selector_mode=mode, artifact=artifact, ranking=[{"pipeline": "zstd", "available": True}])

    monkeypatch.setattr("Compressor.pipeline_runner.select_pipeline", fake_select_pipeline)
    for strategy in ("model", "hybrid", "exhaustive"):
        calls.clear()
        result = run_pipeline(PipelineRunConfig(
            input_dir=source,
            result_dir=tmp_path / f"result_{strategy}",
            strategy=strategy,
            dedup=True,
            block_size_bytes=1024,
            pipelines=["zstd"],
            model_dir=tmp_path / "fake_model",
            top_k=2,
        ))
        assert result.rebuild_result and result.rebuild_result["ok"] is True
        assert calls == [strategy, strategy, strategy]


def test_typed_block_context_uses_legal_block_shape_and_preserves_tail() -> None:
    values = np.arange(12, dtype=np.float32).reshape(3, 4)
    raw = values.tobytes() + b"Z"

    context, metadata = build_context_for_block(
        raw[:16],
        dtype_name="float32",
        full_shape=(3, 4),
        endian="little",
        block_offset=0,
    )

    assert context.dtype_name == "float32"
    assert context.typesize == 4
    assert context.shape == (1, 4)
    assert metadata["shape_source"] == "block_slice"


def test_typed_pipelines_roundtrip_dtype_endian_and_unaligned_tail(tmp_path: Path) -> None:
    from Compressor.container import build_header, encode_metadata
    from Compressor.dedup_manifest import decompress_cpss_blob
    from Compressor.reversible import compress_with_pipeline
    from Compressor.selector import SELECTOR_MODE_CODES

    cases = [
        ("float32", np.arange(16, dtype="<f4").tobytes(), "little", 4),
        ("float64", np.arange(16, dtype=">f8").tobytes(), "big", 8),
        ("uint16", np.arange(16, dtype="<u2").tobytes(), "little", 2),
    ]
    for dtype_name, raw, endian, typesize in cases:
        payload = raw + b"XYZ"
        context, context_metadata = build_context_for_block(
            payload,
            dtype_name=dtype_name,
            full_shape=(4, 4),
            endian=endian,
            block_offset=0,
        )
        artifact = compress_with_pipeline(
            payload,
            "shuffle->bzip2",
            context,
            context_metadata=context_metadata,
            selector_metadata={"mode": "test"},
        )
        metadata_blob = encode_metadata(artifact.metadata)
        header = build_header(
            selector_mode=SELECTOR_MODE_CODES["calibrated_fixed"],
            pipeline_id=artifact.pipeline_id,
            metadata_size=len(metadata_blob),
            tail_size=len(artifact.tail),
            payload_size=len(artifact.payload),
            original_size=len(payload),
        )
        restored = decompress_cpss_blob(header + metadata_blob + artifact.tail + artifact.payload)
        assert restored == payload
        assert context.typesize == typesize
        assert context_metadata["typesize"] == typesize

    text_context, text_metadata = build_context_for_block(b"abc\n" * 8, dtype_name=None)
    assert text_context.dtype_name is None
    assert text_context.typesize == 1
    assert text_metadata["shape_source"] == "byte_fallback"

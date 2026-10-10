from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .container import build_header, encode_metadata
from .cpss_sampling_matrix import (
    DEFAULT_CALIBRATION_BLOCKS,
    DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLING_MODE,
    calibrate_fixed_pipeline,
    compress_records_to_bundle,
    deployable_pipeline_names,
    evaluate_full_data_global_system_oracle,
    make_no_dedup_records,
    scan_dedup_records,
)
from .dedup import FileRecord, UniqueBlockRecord, sha256_file
from .dedup_manifest import rebuild_from_bundle
from .reversible import compress_with_pipeline
from .runtime import PipelineContext, build_context_for_block, canonical_pipeline_name
from .selector import SELECTOR_MODE_CODES, select_pipeline


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_MODEL_DIRS = {
    "science": ROOT / "Model",
    "llm_text": ROOT / "Model" / "scenarios" / "llm_text",
    "metadata": ROOT / "Model" / "scenarios" / "metadata",
}
STRATEGIES = {
    "raw",
    "fixed",
    "fixed_sampling",
    "random_sampling",
    "model",
    "hybrid",
    "exhaustive",
}
SCENARIOS = set(SCENARIO_MODEL_DIRS)
DEFAULT_BLOCK_SIZE_BYTES = 8 * 1024 * 1024


BlockCompressor = Callable[[bytes, UniqueBlockRecord], tuple[bytes, dict[str, Any]]]


@dataclass
class PipelineRunConfig:
    result_dir: Path
    input_dir: Path | None = None
    oss_uri: str | None = None
    scenario: str = "science"
    strategy: str = DEFAULT_SAMPLING_MODE
    dedup: bool = True
    block_size_bytes: int = DEFAULT_BLOCK_SIZE_BYTES
    fixed_pipeline: str = "zstd"
    pipelines: list[str] | None = None
    model_dir: Path | None = None
    top_k: int = 3
    sample_count: int = DEFAULT_CALIBRATION_BLOCKS
    random_seed: int = DEFAULT_RANDOM_SEED
    run_id: str | None = None
    verify_rebuild: bool = True
    keep_rebuild: bool = False
    keep_prepared: bool = False
    dtype_name: str | None = None
    shape: tuple[int, ...] | None = None
    endian: str = "little"
    datasets_metadata_path: Path | None = None
    dataset_slug: str | None = None
    bandwidth_Bps: float | None = None
    receiver_decompress_profile: dict[str, float] | None = None
    full_data_global_oracle: bool = True
    min_expected_speedup: float = 1.0
    guard_margin: float = 0.0


@dataclass
class PipelineRunResult:
    status: str
    run_id: str
    result_dir: Path
    source_dir: Path
    source_manifest_path: Path
    method_metrics_path: Path
    block_metrics_path: Path
    summary_path: Path
    cleanup_audit_path: Path
    bundle_dir: Path | None = None
    bundle_manifest_path: Path | None = None
    method_metrics: dict[str, Any] | None = None
    rebuild_result: dict[str, Any] | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_result_dir(prefix: str = "cpss_pipeline") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "ExperimentRecords" / f"{prefix}_{stamp}"


def normalize_strategy(value: str) -> str:
    strategy = str(value).strip().lower().replace("-", "_")
    aliases = {
        "raw_direct": "raw",
        "native_exhaustive": "exhaustive",
        "cpss_native_exhaustive": "exhaustive",
        "calibrated_fixed": "fixed",
    }
    strategy = aliases.get(strategy, strategy)
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy: {value}")
    return strategy


def parse_pipeline_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [canonical_pipeline_name(item) for item in value.split(",") if item.strip()]


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"object is not JSON serializable: {type(value).__name__}")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _safe_div(numerator: float, denominator: float) -> float | str:
    return float(numerator) / float(denominator) if float(denominator) > 0 else ""


def _copy_tree_for_local_transfer(source: Path, target: Path) -> float:
    started = time.perf_counter()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return time.perf_counter() - started


def _verify_tree_against_manifest(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    file_results = []
    for item in manifest["files"]:
        path = root / item["rel_path"]
        digest = sha256_file(path)
        ok = path.stat().st_size == int(item["size_bytes"]) and digest == item["sha256"]
        if not ok:
            raise ValueError(f"raw verification failed: {item['rel_path']}")
        file_results.append({
            "rel_path": item["rel_path"],
            "size_bytes": path.stat().st_size,
            "sha256": digest,
            "ok": True,
        })
    return {
        "ok": True,
        "file_count": len(file_results),
        "file_results": file_results,
        "verify_time_s": time.perf_counter() - started,
        "decompress_time_s": 0.0,
        "rebuild_time_s": 0.0,
    }


def _predicted_raw_e2e(raw_bytes: int, bandwidth_Bps: float | None) -> float | str:
    if bandwidth_Bps is None:
        return ""
    return _safe_div(raw_bytes, bandwidth_Bps)


def _estimate_selected_pipeline_e2e(calibration: dict[str, Any] | None, stats: dict[str, Any]) -> float | str:
    if not calibration or not calibration.get("sample_count"):
        return ""
    sample_count = int(calibration.get("sample_count") or 0)
    if sample_count <= 0:
        return ""
    raw_bytes = int(stats.get("raw_bytes") or 0)
    sampled_raw = sum(int(row.get("raw_size") or 0) for row in calibration.get("trials", []) if row.get("status") == "ok")
    if sampled_raw <= 0:
        return ""
    selected = calibration.get("selected_pipeline")
    selected_trials = [
        row for row in calibration.get("trials", [])
        if row.get("status") == "ok" and row.get("pipeline_name") == selected
    ]
    sampled_cost = sum(float(row.get("estimated_system_cost_s") or 0.0) for row in selected_trials)
    sampled_raw_selected = sum(int(row.get("raw_size") or 0) for row in selected_trials)
    if sampled_cost <= 0 or sampled_raw_selected <= 0:
        return ""
    return sampled_cost * raw_bytes / sampled_raw_selected


def _decide_raw_bypass(
    *,
    config: PipelineRunConfig,
    stats: dict[str, Any],
    calibration: dict[str, Any] | None,
) -> dict[str, Any]:
    predicted_raw = _predicted_raw_e2e(int(stats.get("raw_bytes") or 0), config.bandwidth_Bps)
    predicted_compressed = _estimate_selected_pipeline_e2e(calibration, stats)
    probe_time = float((calibration or {}).get("calibration_time_s") or 0.0)
    decision = {
        "selected_action": "compress",
        "predicted_raw_e2e": predicted_raw,
        "predicted_compressed_e2e": predicted_compressed,
        "bypass_reason": "",
        "probe_time_s": probe_time,
        "fallback_overhead_s": 0.0,
    }
    if config.strategy not in {"fixed_sampling", "random_sampling"}:
        return decision
    if config.bandwidth_Bps is None:
        decision["bypass_reason"] = "not_evaluated_without_bandwidth"
        return decision
    if predicted_raw == "" or predicted_compressed == "":
        decision["selected_action"] = "raw_bypass"
        decision["bypass_reason"] = "insufficient_prediction"
        decision["fallback_overhead_s"] = probe_time
        return decision
    required = float(predicted_raw) / max(float(config.min_expected_speedup), 1e-12)
    required *= 1.0 - max(0.0, float(config.guard_margin))
    if float(predicted_compressed) >= required:
        decision["selected_action"] = "raw_bypass"
        decision["bypass_reason"] = "predicted_compressed_e2e_not_better_than_raw_guard"
        decision["fallback_overhead_s"] = probe_time
    return decision


def _system_regret(
    selected_e2e: float | str,
    oracle: dict[str, Any] | None,
) -> float | str:
    if selected_e2e == "" or not oracle:
        return ""
    oracle_e2e = float(oracle.get("selected_e2e_estimate_s") or 0.0)
    if oracle_e2e <= 0:
        return ""
    return float(selected_e2e) / oracle_e2e - 1.0


def run_command(command: list[str], *, cwd: Path = ROOT, log: Path | None = None) -> subprocess.CompletedProcess[str]:
    rendered = "$ " + " ".join(str(item) for item in command)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, check=False)
    if log is not None:
        with log.open("a", encoding="utf-8") as handle:
            if result.stdout:
                handle.write(result.stdout)
            if result.stderr:
                handle.write(result.stderr)
            handle.write(f"[exit {result.returncode}]\n")
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {rendered}\n{result.stderr[-2000:]}")
    return result


def _relative_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise NotADirectoryError(root)
    return sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def build_source_manifest(root: Path) -> dict[str, Any]:
    files = []
    total = 0
    for path in _relative_files(root):
        size = path.stat().st_size
        total += size
        files.append({
            "rel_path": path.relative_to(root).as_posix(),
            "size_bytes": size,
            "sha256": sha256_file(path),
        })
    return {
        "source_root": str(root),
        "file_count": len(files),
        "raw_bytes": total,
        "files": files,
    }


def prepare_source(config: PipelineRunConfig, command_log: Path) -> tuple[Path, list[Path], list[dict[str, Any]]]:
    if config.input_dir is not None and config.oss_uri:
        raise ValueError("use either input_dir or oss_uri, not both")
    if config.input_dir is None and not config.oss_uri:
        raise ValueError("input_dir or oss_uri is required")
    if config.input_dir is not None:
        return config.input_dir.resolve(), [], [{"action": "use_local_input", "path": str(config.input_dir.resolve())}]

    assert config.oss_uri is not None
    prepared = config.result_dir / "_prepared"
    archive = prepared / "source.tar.zst"
    extracted = prepared / "extracted"
    prepared.mkdir(parents=True, exist_ok=True)
    stat = run_command(["ossutil", "stat", config.oss_uri], log=command_log)
    (prepared / "oss_stat.txt").write_text(stat.stdout + stat.stderr, encoding="utf-8")
    run_command(["ossutil", "cp", config.oss_uri, str(archive)], log=command_log)
    run_command(["zstd", "-t", str(archive)], log=command_log)
    extracted.mkdir(parents=True, exist_ok=True)
    run_command(["tar", "--zstd", "-xf", str(archive), "-C", str(extracted)], log=command_log)
    return extracted.resolve(), [prepared], [
        {"action": "oss_stat", "uri": config.oss_uri, "path": str(prepared / "oss_stat.txt")},
        {"action": "oss_download", "uri": config.oss_uri, "path": str(archive)},
        {"action": "zstd_test", "path": str(archive)},
        {"action": "tar_extract", "path": str(extracted)},
    ]


def _block_context(
    raw: bytes,
    *,
    scenario: str,
    source_group: str = "unified_cpss_pipeline",
    dtype_name: str | None = None,
    shape: tuple[int, ...] | None = None,
    endian: str = "little",
    block_offset: int = 0,
) -> tuple[PipelineContext, dict[str, Any]]:
    context, metadata = build_context_for_block(
        raw,
        dtype_name=dtype_name,
        full_shape=shape,
        endian=endian,
        block_offset=block_offset,
        scenario=scenario,
        source_group=source_group,
    )
    metadata["original_suffix"] = ".bin"
    return context, metadata


def _load_dataset_metadata(config: PipelineRunConfig) -> dict[str, Any]:
    path = config.datasets_metadata_path
    if path is None:
        candidate = Path(r"E:\Dataset\DCC_upload\full-v1\metadata\datasets.json")
        path = candidate if candidate.exists() else None
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("datasets", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        return {}
    slug = config.dataset_slug
    if slug:
        return next((dict(row) for row in rows if row.get("slug") == slug), {})
    if config.input_dir:
        name = config.input_dir.name.lower()
        for row in rows:
            if str(row.get("slug", "")).lower() == name:
                return dict(row)
    return {}


def _effective_data_metadata(config: PipelineRunConfig) -> dict[str, Any]:
    row = _load_dataset_metadata(config)
    dtype_name = config.dtype_name if config.dtype_name is not None else row.get("dtype")
    shape_value = config.shape if config.shape is not None else row.get("shape")
    shape = tuple(int(item) for item in shape_value) if shape_value else None
    endian = str(row.get("endian") or config.endian or "little")
    if config.endian != "little":
        endian = config.endian
    return {
        "dtype_name": dtype_name,
        "shape": shape,
        "endian": endian if endian in {"little", "big"} else "little",
        "metadata_source": "cli" if config.dtype_name or config.shape else ("datasets.json" if row else "byte_stream_default"),
        "dataset_slug": row.get("slug") or config.dataset_slug or "",
    }


def _block_offset(files: list[FileRecord], block: UniqueBlockRecord) -> int:
    file_map = {item.file_id: item for item in files}
    return int(file_map[block.source_file_id].blocks[block.source_block_index].offset)


def _artifact_to_blob(raw: bytes, artifact: Any, selector_mode: str) -> bytes:
    metadata_blob = encode_metadata(artifact.metadata)
    header = build_header(
        selector_mode=SELECTOR_MODE_CODES.get(selector_mode, 0),
        pipeline_id=artifact.pipeline_id,
        metadata_size=len(metadata_blob),
        tail_size=len(artifact.tail),
        payload_size=len(artifact.payload),
        original_size=len(raw),
    )
    return header + metadata_blob + artifact.tail + artifact.payload


def make_fixed_pipeline_compressor(
    pipeline_name: str,
    *,
    scenario: str,
    selector_metadata: dict[str, Any],
    files: list[FileRecord] | None = None,
    dtype_name: str | None = None,
    shape: tuple[int, ...] | None = None,
    endian: str = "little",
) -> BlockCompressor:
    pipeline_name = canonical_pipeline_name(pipeline_name)

    def _compress(raw: bytes, _block: UniqueBlockRecord) -> tuple[bytes, dict[str, Any]]:
        context, context_metadata = _block_context(
            raw,
            scenario=scenario,
            dtype_name=dtype_name,
            shape=shape,
            endian=endian,
            block_offset=_block_offset(files, _block) if files else 0,
        )
        artifact = compress_with_pipeline(
            raw,
            pipeline_name,
            context,
            context_metadata=context_metadata,
            selector_metadata=dict(selector_metadata),
        )
        blob = _artifact_to_blob(raw, artifact, selector_metadata.get("mode", "calibrated_fixed"))
        return blob, {
            "pipeline_name": artifact.pipeline_name,
            "pipeline_id": artifact.pipeline_id,
            "selector_mode": selector_metadata.get("mode", "calibrated_fixed"),
        }

    return _compress


def make_selector_compressor(
    *,
    mode: str,
    scenario: str,
    model_dir: Path | None,
    candidates: list[str],
    top_k: int,
    files: list[FileRecord] | None = None,
    dtype_name: str | None = None,
    shape: tuple[int, ...] | None = None,
    endian: str = "little",
) -> BlockCompressor:
    def _compress(raw: bytes, _block: UniqueBlockRecord) -> tuple[bytes, dict[str, Any]]:
        context, context_metadata = _block_context(
            raw,
            scenario=scenario,
            dtype_name=dtype_name,
            shape=shape,
            endian=endian,
            block_offset=_block_offset(files, _block) if files else 0,
        )
        selection = select_pipeline(
            raw,
            context,
            context_metadata,
            mode=mode,
            model_dir=model_dir,
            top_k=top_k,
            candidates=candidates,
        )
        selection.artifact.selector_metadata.update({
            "requested_mode": mode,
            "resolved_mode": selection.selector_mode,
            "scenario": scenario,
            "top_k": int(top_k),
            "ranking": selection.ranking,
        })
        blob = _artifact_to_blob(raw, selection.artifact, selection.selector_mode)
        return blob, {
            "pipeline_name": selection.artifact.pipeline_name,
            "pipeline_id": selection.artifact.pipeline_id,
            "selector_mode": selection.selector_mode,
        }

    return _compress


def _scan_records(
    source_dir: Path,
    block_size_bytes: int,
    *,
    dedup: bool,
) -> tuple[list[FileRecord], list[UniqueBlockRecord], dict[str, Any], float]:
    started = time.perf_counter()
    if dedup:
        files, blocks, stats = scan_dedup_records(source_dir, block_size_bytes)
    else:
        files, blocks, stats = make_no_dedup_records(source_dir, block_size_bytes)
    return files, blocks, dict(stats), time.perf_counter() - started


def _resolve_model_dir(config: PipelineRunConfig) -> Path | None:
    if config.strategy not in {"model", "hybrid"}:
        return None
    if config.model_dir is not None:
        return config.model_dir.resolve()
    return SCENARIO_MODEL_DIRS[config.scenario]


def _build_block_compressor(
    config: PipelineRunConfig,
    source_dir: Path,
    files: list[FileRecord],
    blocks: list[UniqueBlockRecord],
) -> tuple[BlockCompressor, dict[str, Any] | None, list[dict[str, Any]], str, str]:
    candidates = [canonical_pipeline_name(item) for item in (config.pipelines or deployable_pipeline_names())]
    strategy = config.strategy
    data_meta = _effective_data_metadata(config)
    dtype_name = data_meta["dtype_name"]
    shape = data_meta["shape"]
    endian = data_meta["endian"]
    if strategy == "fixed":
        pipeline = canonical_pipeline_name(config.fixed_pipeline)
        return (
            make_fixed_pipeline_compressor(
                pipeline,
                scenario=config.scenario,
                selector_metadata={"mode": "calibrated_fixed", "strategy": "fixed", "fixed_pipeline": pipeline},
                files=files,
                dtype_name=dtype_name,
                shape=shape,
                endian=endian,
            ),
            {"strategy": "fixed", "selected_pipeline": pipeline, "trials": [], "data_metadata": data_meta},
            [],
            "fixed",
            pipeline,
        )

    if strategy in {"fixed_sampling", "random_sampling"}:
        calibration = calibrate_fixed_pipeline(
            source_dir,
            files,
            blocks,
            sampling_mode=strategy,
            candidates=candidates,
            sample_count=config.sample_count,
            seed=config.random_seed if strategy == "random_sampling" else None,
            bandwidth_Bps=config.bandwidth_Bps,
            receiver_decompress_profile=config.receiver_decompress_profile,
            dtype_name=dtype_name,
            full_shape=shape,
            endian=endian,
        )
        selected = calibration.get("selected_pipeline") or canonical_pipeline_name(config.fixed_pipeline)
        return (
            make_fixed_pipeline_compressor(
                selected,
                scenario=config.scenario,
                selector_metadata={
                    "mode": "calibrated_fixed",
                    "strategy": strategy,
                    "sampling_mode": strategy,
                    "seed": config.random_seed if strategy == "random_sampling" else None,
                    "fixed_pipeline": selected,
                },
                files=files,
                dtype_name=dtype_name,
                shape=shape,
                endian=endian,
            ),
            calibration,
            list(calibration.get("trials") or []),
            strategy,
            selected,
        )

    model_dir = _resolve_model_dir(config)
    return (
        make_selector_compressor(
            mode=strategy,
            scenario=config.scenario,
            model_dir=model_dir,
            candidates=candidates,
            top_k=config.top_k,
            files=files,
            dtype_name=dtype_name,
            shape=shape,
            endian=endian,
        ),
        {"strategy": strategy, "selected_pipeline": "", "trials": []},
        [],
        strategy,
        "",
    )


def _method_row(
    *,
    config: PipelineRunConfig,
    run_id: str,
    source_dir: Path,
    status: str,
    stats: dict[str, Any],
    scan_time_s: float,
    strategy: str,
    dedup_effective: bool,
    selected_pipeline: str = "",
    calibration: dict[str, Any] | None = None,
    compress_stats: dict[str, Any] | None = None,
    rebuild_result: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    oracle: dict[str, Any] | None = None,
    wall: dict[str, Any] | None = None,
    data_metadata: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    calibration = dict(calibration or {})
    compress_stats = dict(compress_stats or {})
    compressed = int(compress_stats.get("compressed_unique_bytes") or 0)
    raw_bytes = int(stats.get("raw_bytes") or 0)
    transferred = int(compress_stats.get("transferred_bytes") or raw_bytes)
    decision = dict(decision or {})
    wall = dict(wall or {})
    data_metadata = dict(data_metadata or {})
    cold_e2e = wall.get("cold_e2e_wall_clock_s", wall.get("output_ready_wall_clock_s", ""))
    warm_e2e = wall.get("warm_e2e_wall_clock_s", cold_e2e)
    output_ready = wall.get("output_ready_wall_clock_s", cold_e2e)
    verification_done = wall.get("verification_done_wall_clock_s", output_ready)
    transfer_wall = wall.get("transfer_wall_clock_s", "")
    raw_ref = wall.get("raw_e2e_wall_clock_s", "")
    selected_prediction = decision.get("predicted_compressed_e2e", "")
    oracle_regret = _system_regret(selected_prediction, oracle)
    row = {
        "run_id": run_id,
        "status": status,
        "error": error,
        "input_dir": str(source_dir),
        "scenario": config.scenario,
        "strategy": strategy,
        "dedup": "on" if dedup_effective else "off",
        "block_size_bytes": config.block_size_bytes,
        "file_count": int(stats.get("file_count") or 0),
        "raw_bytes": raw_bytes,
        "block_total_count": int(stats.get("block_total_count") or 0),
        "block_unique_count": int(stats.get("block_unique_count") or 0),
        "block_duplicate_count": int(stats.get("block_duplicate_count") or 0),
        "duplicate_file_count": int(stats.get("duplicate_file_count") or 0),
        "file_dedup_saved_bytes": int(stats.get("file_dedup_saved_bytes") or 0),
        "block_dedup_saved_bytes": int(stats.get("block_dedup_saved_bytes") or 0),
        "unique_raw_bytes": int(stats.get("unique_raw_bytes") or raw_bytes),
        "compressed_unique_bytes": compressed,
        "manifest_bytes": int(compress_stats.get("manifest_bytes") or 0),
        "transferred_bytes": transferred,
        "original_bytes": raw_bytes,
        "selected_pipeline": selected_pipeline,
        "selected_action": decision.get("selected_action", "compress" if strategy != "raw" else "raw_bypass"),
        "predicted_raw_e2e": decision.get("predicted_raw_e2e", ""),
        "predicted_compressed_e2e": decision.get("predicted_compressed_e2e", ""),
        "bypass_reason": decision.get("bypass_reason", ""),
        "probe_time_s": decision.get("probe_time_s", float(calibration.get("calibration_time_s") or 0.0)),
        "fallback_overhead_s": decision.get("fallback_overhead_s", 0.0),
        "sampling_mode": calibration.get("sampling_mode", ""),
        "seed": calibration.get("seed", ""),
        "sample_count": calibration.get("sample_count", ""),
        "sample_block_indices": json.dumps(calibration.get("sample_block_indices", [])),
        "calibration_time_s": float(calibration.get("calibration_time_s") or 0.0),
        "scan_time_s": scan_time_s,
        "compress_time_s": float(compress_stats.get("compress_time_s") or 0.0),
        "rebuild_decompress_time_s": float((rebuild_result or {}).get("decompress_time_s") or 0.0),
        "rebuild_time_s": float((rebuild_result or {}).get("rebuild_time_s") or 0.0),
        "verify_time_s": float((rebuild_result or {}).get("verify_time_s") or 0.0),
        "roundtrip_ok": bool((rebuild_result or {}).get("ok") or strategy == "raw"),
        "transfer_wall_clock_s": transfer_wall,
        "e2e_wall_clock_s": cold_e2e,
        "output_ready_wall_clock_s": output_ready,
        "verification_done_wall_clock_s": verification_done,
        "cold_e2e_wall_clock_s": cold_e2e,
        "warm_e2e_wall_clock_s": warm_e2e,
        "raw_e2e_wall_clock_s": raw_ref,
        "transfer_goodput_Bps": _safe_div(raw_bytes, float(transfer_wall)) if transfer_wall != "" else "",
        "e2e_goodput_Bps": _safe_div(raw_bytes, float(cold_e2e)) if cold_e2e != "" else "",
        "e2e_speedup_vs_raw": _safe_div(float(raw_ref), float(cold_e2e)) if raw_ref != "" and cold_e2e != "" else "",
        "byte_reduction_ratio": raw_bytes / transferred if transferred else "",
        "full_data_global_oracle_pipeline": (oracle or {}).get("selected_pipeline", ""),
        "full_data_global_oracle_e2e_s": (oracle or {}).get("selected_e2e_estimate_s", ""),
        "system_regret_vs_full_data_global_oracle": oracle_regret,
        "unsampled_block_system_regret": oracle_regret,
        "per_block_exhaustive_oracle_role": "theoretical_upper_bound" if strategy == "exhaustive" else "",
        "data_dtype": data_metadata.get("dtype_name") or "",
        "data_shape": json.dumps(list(data_metadata.get("shape") or [])),
        "data_endian": data_metadata.get("endian", ""),
        "data_metadata_source": data_metadata.get("metadata_source", ""),
        "compression_ratio_payload": raw_bytes / compressed if compressed else "",
        "logical_bandwidth_gain_vs_raw_bytes": raw_bytes / transferred if transferred else "",
        "deprecated_logical_bandwidth_gain_vs_raw_bytes": raw_bytes / transferred if transferred else "",
        "deprecated_e2e_excluding_calibration_s": warm_e2e,
        "deprecated_e2e_including_calibration_s": cold_e2e,
        "deprecated_fields": json.dumps([
            "logical_bandwidth_gain_vs_raw_bytes",
            "deprecated_e2e_excluding_calibration_s",
            "deprecated_e2e_including_calibration_s",
        ]),
    }
    return row


def write_summary(path: Path, row: dict[str, Any], *, bundle_manifest: Path | None, source_manifest: Path) -> None:
    ratio = row.get("compression_ratio_payload") or ""
    byte_reduction = row.get("byte_reduction_ratio") or ""
    lines = [
        "# 统一 CPSS 数据处理流程报告",
        "",
        f"- status: `{row['status']}`",
        f"- scenario: `{row['scenario']}`",
        f"- strategy: `{row['strategy']}`",
        f"- dedup: `{row['dedup']}`",
        f"- input: `{row['input_dir']}`",
        f"- source manifest: `{source_manifest}`",
        f"- bundle manifest: `{bundle_manifest}`" if bundle_manifest else "- bundle manifest: `not_applicable_for_raw`",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| raw bytes | {row['raw_bytes']} |",
        f"| file count | {row['file_count']} |",
        f"| block total | {row['block_total_count']} |",
        f"| unique blocks | {row['block_unique_count']} |",
        f"| duplicate files | {row['duplicate_file_count']} |",
        f"| duplicate blocks | {row['block_duplicate_count']} |",
        f"| transferred bytes | {row['transferred_bytes']} |",
        f"| compression ratio | {ratio} |",
        f"| byte reduction ratio | {byte_reduction} |",
        f"| transfer goodput Bps | {row.get('transfer_goodput_Bps', '')} |",
        f"| e2e goodput Bps | {row.get('e2e_goodput_Bps', '')} |",
        f"| e2e speedup vs raw | {row.get('e2e_speedup_vs_raw', '')} |",
        f"| cold e2e wall-clock s | {row.get('cold_e2e_wall_clock_s', '')} |",
        f"| warm e2e wall-clock s | {row.get('warm_e2e_wall_clock_s', '')} |",
        f"| selected action | {row.get('selected_action', '')} |",
        f"| calibration time s | {row['calibration_time_s']} |",
        f"| compress time s | {row['compress_time_s']} |",
        f"| roundtrip ok | {row['roundtrip_ok']} |",
        "",
        "说明：本入口只统一 CPSS 本地处理链路；GridFlux 远端传输仍由现有 legacy 矩阵脚本承接，直到远端控制面代码收敛进同一 runner。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(config: PipelineRunConfig) -> PipelineRunResult:
    config.strategy = normalize_strategy(config.strategy)
    config.scenario = str(config.scenario).strip().lower()
    if config.scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {config.scenario}")
    if config.block_size_bytes <= 0:
        raise ValueError("block_size_bytes must be positive")
    if config.bandwidth_Bps is not None and config.bandwidth_Bps <= 0:
        raise ValueError("bandwidth_Bps must be positive when configured")
    run_id = config.run_id or config.result_dir.name
    result_dir = config.result_dir.resolve()
    result_dir.mkdir(parents=True, exist_ok=True)

    command_log = result_dir / "command_log.txt"
    cleanup_actions: list[dict[str, Any]] = []
    prepared_paths: list[Path] = []
    bundle_dir: Path | None = None
    bundle_manifest_path: Path | None = None
    rebuild_result: dict[str, Any] | None = None

    source_dir, prepared_paths, prepare_actions = prepare_source(config, command_log)
    cleanup_actions.extend(prepare_actions)
    source_manifest = build_source_manifest(source_dir)
    source_manifest_path = result_dir / "source_manifest.json"
    write_json(source_manifest_path, source_manifest)

    source_stats = {
        "raw_bytes": source_manifest["raw_bytes"],
        "file_count": source_manifest["file_count"],
        "unique_file_count": source_manifest["file_count"],
        "duplicate_file_count": 0,
        "file_dedup_saved_bytes": 0,
        "block_total_count": 0,
        "block_unique_count": 0,
        "block_duplicate_count": 0,
        "block_dedup_saved_bytes": 0,
        "unique_raw_bytes": source_manifest["raw_bytes"],
    }
    block_rows: list[dict[str, Any]] = []
    data_metadata = _effective_data_metadata(config)

    if config.strategy == "raw":
        method_started = time.perf_counter()
        raw_transfer_dir = result_dir / "raw_transfer"
        transfer_time_s = _copy_tree_for_local_transfer(source_dir, raw_transfer_dir)
        output_ready_s = time.perf_counter() - method_started
        if config.verify_rebuild:
            rebuild_result = _verify_tree_against_manifest(raw_transfer_dir, source_manifest)
        verification_done_s = time.perf_counter() - method_started
        if not config.keep_rebuild and raw_transfer_dir.exists():
            shutil.rmtree(raw_transfer_dir)
            cleanup_actions.append({"action": "remove_raw_transfer_dir", "path": str(raw_transfer_dir), "status": "removed"})
        raw_wall = {
            "transfer_wall_clock_s": transfer_time_s,
            "output_ready_wall_clock_s": output_ready_s,
            "verification_done_wall_clock_s": verification_done_s,
            "cold_e2e_wall_clock_s": output_ready_s,
            "warm_e2e_wall_clock_s": output_ready_s,
            "raw_e2e_wall_clock_s": output_ready_s,
        }
        method_row = _method_row(
            config=config,
            run_id=run_id,
            source_dir=source_dir,
            status="completed",
            stats=source_stats,
            scan_time_s=0.0,
            strategy="raw",
            dedup_effective=False,
            rebuild_result=rebuild_result,
            decision={
                "selected_action": "raw_bypass",
                "predicted_raw_e2e": _predicted_raw_e2e(source_stats["raw_bytes"], config.bandwidth_Bps),
                "predicted_compressed_e2e": "",
                "bypass_reason": "explicit_raw_strategy",
                "probe_time_s": 0.0,
            },
            wall=raw_wall,
            data_metadata=data_metadata,
        )
    else:
        raw_reference_start = time.perf_counter()
        raw_reference_dir = result_dir / "_raw_reference_transfer"
        raw_reference_transfer_s = _copy_tree_for_local_transfer(source_dir, raw_reference_dir)
        raw_output_ready_s = time.perf_counter() - raw_reference_start
        raw_verify_result = _verify_tree_against_manifest(raw_reference_dir, source_manifest) if config.verify_rebuild else None
        raw_verification_done_s = time.perf_counter() - raw_reference_start
        if raw_reference_dir.exists():
            shutil.rmtree(raw_reference_dir)
            cleanup_actions.append({"action": "remove_raw_reference_dir", "path": str(raw_reference_dir), "status": "removed"})
        cpss_started = time.perf_counter()
        files, blocks, stats, scan_time_s = _scan_records(
            source_dir,
            config.block_size_bytes,
            dedup=bool(config.dedup),
        )
        block_compressor, calibration, calibration_rows, strategy_name, selected_pipeline = _build_block_compressor(
            config,
            source_dir,
            files,
            blocks,
        )
        oracle: dict[str, Any] | None = None
        candidates = [canonical_pipeline_name(item) for item in (config.pipelines or deployable_pipeline_names())]
        def evaluate_oracle_after_timing() -> dict[str, Any] | None:
            if not config.full_data_global_oracle or not config.bandwidth_Bps:
                return None
            result = evaluate_full_data_global_system_oracle(
                source_dir,
                files,
                blocks,
                candidates=candidates,
                bandwidth_Bps=float(config.bandwidth_Bps),
                receiver_decompress_profile=config.receiver_decompress_profile,
                dtype_name=data_metadata.get("dtype_name"),
                full_shape=data_metadata.get("shape"),
                endian=data_metadata.get("endian") or "little",
            )
            write_json(result_dir / "full_data_global_oracle.json", result)
            return result
        decision = _decide_raw_bypass(config=config, stats=stats, calibration=calibration)
        for row in calibration_rows:
            row.setdefault("run_id", run_id)
        if calibration_rows:
            write_csv(result_dir / "calibration.csv", calibration_rows)
        if decision["selected_action"] == "raw_bypass":
            method_started = time.perf_counter()
            raw_transfer_dir = result_dir / "raw_bypass_transfer"
            transfer_time_s = _copy_tree_for_local_transfer(source_dir, raw_transfer_dir)
            warm_output_ready_s = time.perf_counter() - method_started
            output_ready_s = time.perf_counter() - cpss_started
            if config.verify_rebuild:
                rebuild_result = _verify_tree_against_manifest(raw_transfer_dir, source_manifest)
            verification_done_s = time.perf_counter() - cpss_started
            if not config.keep_rebuild and raw_transfer_dir.exists():
                shutil.rmtree(raw_transfer_dir)
                cleanup_actions.append({"action": "remove_raw_bypass_transfer_dir", "path": str(raw_transfer_dir), "status": "removed"})
            decision["fallback_overhead_s"] = float(decision.get("probe_time_s") or 0.0)
            wall = {
                "transfer_wall_clock_s": transfer_time_s,
                "output_ready_wall_clock_s": output_ready_s,
                "verification_done_wall_clock_s": verification_done_s,
                "cold_e2e_wall_clock_s": output_ready_s,
                "warm_e2e_wall_clock_s": warm_output_ready_s,
                "raw_e2e_wall_clock_s": raw_output_ready_s,
            }
            oracle = evaluate_oracle_after_timing()
            method_row = _method_row(
                config=config,
                run_id=run_id,
                source_dir=source_dir,
                status="completed",
                stats=stats,
                scan_time_s=scan_time_s,
                strategy=strategy_name,
                dedup_effective=False,
                selected_pipeline=selected_pipeline,
                calibration=calibration,
                compress_stats={"transferred_bytes": stats.get("raw_bytes", 0), "compressed_unique_bytes": 0},
                rebuild_result=rebuild_result,
                decision=decision,
                oracle=oracle,
                wall=wall,
                data_metadata=data_metadata,
            )
        else:
            method_started = time.perf_counter()
            bundle_dir = result_dir / "bundle"
            manifest, compress_stats, block_rows = compress_records_to_bundle(
                root=source_dir,
                bundle=bundle_dir,
                run_id=run_id,
                block_size_bytes=config.block_size_bytes,
                files=files,
                unique_blocks=blocks,
                stats=stats,
                dedup_enabled=bool(config.dedup),
                compression_strategy=strategy_name,
                block_compressor=block_compressor,
                created_at=utc_now(),
                calibration=calibration,
                data_metadata=data_metadata,
            )
            bundle_manifest_path = bundle_dir / "manifest.json"
            transfer_source = bundle_dir
            transfer_dir = result_dir / "_bundle_transfer"
            transfer_time_s = _copy_tree_for_local_transfer(transfer_source, transfer_dir)
            warm_body_s = time.perf_counter() - method_started
            output_ready_s = time.perf_counter() - cpss_started
            if config.verify_rebuild:
                rebuild_dir = result_dir / "rebuild"
                rebuild_result = rebuild_from_bundle(transfer_dir, rebuild_dir, manifest=manifest)
                if not config.keep_rebuild and rebuild_dir.exists():
                    shutil.rmtree(rebuild_dir)
                    cleanup_actions.append({"action": "remove_rebuild_dir", "path": str(rebuild_dir), "status": "removed"})
            verification_done_s = time.perf_counter() - cpss_started
            if transfer_dir.exists():
                shutil.rmtree(transfer_dir)
                cleanup_actions.append({"action": "remove_bundle_transfer_dir", "path": str(transfer_dir), "status": "removed"})
            wall = {
                "transfer_wall_clock_s": transfer_time_s,
                "output_ready_wall_clock_s": output_ready_s,
                "verification_done_wall_clock_s": verification_done_s,
                "cold_e2e_wall_clock_s": output_ready_s,
                "warm_e2e_wall_clock_s": scan_time_s + warm_body_s,
                "raw_e2e_wall_clock_s": raw_output_ready_s,
            }
            for row in block_rows:
                row["data_dtype"] = data_metadata.get("dtype_name") or ""
                row["data_endian"] = data_metadata.get("endian", "")
            oracle = evaluate_oracle_after_timing()
            method_row = _method_row(
                config=config,
                run_id=run_id,
                source_dir=source_dir,
                status="completed",
                stats=stats,
                scan_time_s=scan_time_s,
                strategy=strategy_name,
                dedup_effective=bool(config.dedup),
                selected_pipeline=selected_pipeline,
                calibration=calibration,
                compress_stats=compress_stats,
                rebuild_result=rebuild_result,
                decision=decision,
                oracle=oracle,
                wall=wall,
                data_metadata=data_metadata,
            )

    block_metrics_path = result_dir / "block_metrics.csv"
    method_metrics_path = result_dir / "method_metrics.csv"
    summary_path = result_dir / "summary_zh.md"
    cleanup_audit_path = result_dir / "cleanup_audit.json"
    write_csv(block_metrics_path, block_rows)
    write_csv(method_metrics_path, [method_row])
    write_summary(summary_path, method_row, bundle_manifest=bundle_manifest_path, source_manifest=source_manifest_path)

    if prepared_paths and not config.keep_prepared:
        for path in prepared_paths:
            if path.exists():
                shutil.rmtree(path)
                cleanup_actions.append({"action": "remove_prepared_path", "path": str(path), "status": "removed"})
    write_json(cleanup_audit_path, {
        "run_id": run_id,
        "created_at": utc_now(),
        "actions": cleanup_actions,
        "prepared_paths_remaining": [str(path) for path in prepared_paths if path.exists()],
    })

    return PipelineRunResult(
        status="completed",
        run_id=run_id,
        result_dir=result_dir,
        source_dir=source_dir,
        source_manifest_path=source_manifest_path,
        method_metrics_path=method_metrics_path,
        block_metrics_path=block_metrics_path,
        summary_path=summary_path,
        cleanup_audit_path=cleanup_audit_path,
        bundle_dir=bundle_dir,
        bundle_manifest_path=bundle_manifest_path,
        method_metrics=method_row,
        rebuild_result=rebuild_result,
    )

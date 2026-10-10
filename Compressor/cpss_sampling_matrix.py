from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .container import build_header, encode_metadata
from .dedup import (
    BlockRef,
    FileRecord,
    UniqueBlockRecord,
    read_block,
    scan_directory,
    sha256_bytes,
    sha256_file,
)
from .dedup_manifest import build_manifest, write_manifest
from .decision_features import decision_feature_bucket, extract_decision_features
from .decision_memory import DecisionKey, DecisionMemory
from .deploy_registry import DEPLOYABLE_PIPELINES
from .reversible import compress_with_pipeline
from .runtime import PipelineContext, build_context_for_block, canonical_pipeline_name
from .selector import SELECTOR_MODE_CODES


DEFAULT_CALIBRATION_BLOCKS = 5
PRIMARY_RANDOM_SEED = 20260727
DEFAULT_SAMPLING_MODE = "random_sampling"
DEFAULT_RANDOM_SEED = PRIMARY_RANDOM_SEED
DECISION_MEMORY_RUNTIME_VERSION = "native-exhaustive-v2"

BlockCompressor = Callable[[bytes, UniqueBlockRecord], tuple[bytes, dict[str, Any]]]


def _decision_memory_runtime_signature() -> str:
    distribution_names = ("numpy", "Brotli", "python-snappy", "zstandard", "imagecodecs", "lz4")
    versions: list[str] = []
    for name in distribution_names:
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "unavailable"
        versions.append(f"{name}={version}")
    return (
        f"{DECISION_MEMORY_RUNTIME_VERSION};python={sys.version_info.major}.{sys.version_info.minor};"
        + ";".join(versions)
    )


def system_cost_seconds(
    compress_time_s: float,
    compressed_bytes: int,
    decompress_time_s: float,
    bandwidth_Bps: float | None,
) -> float:
    if bandwidth_Bps is None or float(bandwidth_Bps) <= 0:
        raise ValueError("bandwidth_Bps must be explicitly configured and positive")
    return float(compress_time_s) + int(compressed_bytes) / float(bandwidth_Bps) + float(decompress_time_s)


def deployable_pipeline_names() -> list[str]:
    return [row.name for row in DEPLOYABLE_PIPELINES]


def fixed_sample_indices(total_blocks: int, count: int = DEFAULT_CALIBRATION_BLOCKS) -> list[int]:
    if total_blocks <= 0 or count <= 0:
        return []
    if total_blocks == 1:
        return [0]
    raw_positions = [0.0, 0.25, 0.5, 0.75, 1.0]
    if count != DEFAULT_CALIBRATION_BLOCKS:
        raw_positions = [i / max(1, count - 1) for i in range(count)]
    indices: list[int] = []
    for position in raw_positions[:count]:
        index = int(round(position * (total_blocks - 1)))
        if index not in indices:
            indices.append(index)
    return indices


def random_sample_indices(
    total_blocks: int,
    *,
    seed: int = PRIMARY_RANDOM_SEED,
    count: int = DEFAULT_CALIBRATION_BLOCKS,
) -> list[int]:
    if total_blocks <= 0 or count <= 0:
        return []
    rng = random.Random(int(seed))
    return sorted(rng.sample(range(total_blocks), min(int(count), total_blocks)))


def _iter_blocks(path: Path, block_size: int) -> Iterable[tuple[int, bytes]]:
    offset = 0
    with path.open("rb") as handle:
        while True:
            payload = handle.read(block_size)
            if not payload:
                return
            yield offset, payload
            offset += len(payload)


def make_no_dedup_records(root: Path, block_size: int) -> tuple[list[FileRecord], list[UniqueBlockRecord], dict[str, int]]:
    root = root.resolve()
    files: list[FileRecord] = []
    unique_blocks: list[UniqueBlockRecord] = []
    raw_bytes = 0
    block_count = 0
    for file_number, path in enumerate(
        sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink()),
        start=1,
    ):
        stat = path.stat()
        file_id = f"file_{file_number:06d}"
        record = FileRecord(
            file_id=file_id,
            rel_path=path.relative_to(root).as_posix(),
            size_bytes=stat.st_size,
            sha256=sha256_file(path),
            is_duplicate_file=False,
            duplicate_of_file_id=None,
            mode=stat.st_mode & 0o7777,
            mtime_ns=stat.st_mtime_ns,
        )
        for block_index, (offset, payload) in enumerate(_iter_blocks(path, block_size)):
            block_hash = sha256_bytes(payload)
            unique_id = f"block_{len(unique_blocks) + 1:06d}"
            unique_blocks.append(
                UniqueBlockRecord(
                    unique_block_id=unique_id,
                    block_hash=block_hash,
                    raw_size=len(payload),
                    source_file_id=file_id,
                    source_block_index=block_index,
                )
            )
            record.blocks.append(BlockRef(block_index, offset, len(payload), block_hash, False, unique_id))
            block_count += 1
        raw_bytes += stat.st_size
        files.append(record)
    return files, unique_blocks, {
        "raw_bytes": raw_bytes,
        "file_count": len(files),
        "unique_file_count": len(files),
        "duplicate_file_count": 0,
        "file_dedup_saved_bytes": 0,
        "block_total_count": block_count,
        "block_unique_count": block_count,
        "block_duplicate_count": 0,
        "block_dedup_saved_bytes": 0,
        "unique_raw_bytes": raw_bytes,
    }


def scan_dedup_records(root: Path, block_size: int) -> tuple[list[FileRecord], list[UniqueBlockRecord], dict[str, int]]:
    files, unique_blocks, stats = scan_directory(root, block_size)
    return files, unique_blocks, stats.as_dict()


def source_block(root: Path, records: dict[str, FileRecord], item: UniqueBlockRecord) -> bytes:
    record = records[item.source_file_id]
    return read_block(root / record.rel_path, record.blocks[item.source_block_index])


def make_block_context(
    raw: bytes,
    *,
    source_group: str = "cpss_sampling_dedup_matrix",
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    block_offset: int = 0,
) -> tuple[PipelineContext, dict[str, Any]]:
    return build_context_for_block(
        raw,
        dtype_name=dtype_name,
        full_shape=full_shape,
        endian=endian,
        block_offset=block_offset,
        scenario="science",
        source_group=source_group,
    )


def artifact_to_blob(raw: bytes, artifact: Any, selector_mode: str) -> bytes:
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


def compress_fixed_pipeline_blob(
    raw: bytes,
    pipeline_name: str,
    *,
    selector_mode: str = "calibrated_fixed",
    selector_metadata: dict[str, Any] | None = None,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    block_offset: int = 0,
) -> tuple[bytes, dict[str, Any]]:
    pipeline_name = canonical_pipeline_name(pipeline_name)
    context, context_metadata = make_block_context(
        raw,
        dtype_name=dtype_name,
        full_shape=full_shape,
        endian=endian,
        block_offset=block_offset,
    )
    artifact = compress_with_pipeline(
        raw,
        pipeline_name,
        context,
        context_metadata=context_metadata,
        selector_metadata=selector_metadata or {"mode": selector_mode, "pipeline": pipeline_name},
    )
    blob = artifact_to_blob(raw, artifact, selector_mode)
    return blob, {
        "pipeline_name": artifact.pipeline_name,
        "pipeline_id": artifact.pipeline_id,
        "selector_mode": selector_mode,
    }


def compress_exhaustive_blob(
    raw: bytes,
    candidates: list[str],
    *,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    block_offset: int = 0,
    bandwidth_Bps: float | None = None,
    receiver_decompress_profile: dict[str, float] | None = None,
) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    ranking: list[dict[str, Any]] = []
    best_blob: bytes | None = None
    best_meta: dict[str, Any] | None = None
    best_size: int | None = None
    for pipeline in candidates:
        started = time.perf_counter()
        try:
            blob, meta = compress_fixed_pipeline_blob(
                raw,
                pipeline,
                selector_mode="exhaustive",
                selector_metadata={"mode": "exhaustive", "candidate_count": len(candidates)},
                dtype_name=dtype_name,
                full_shape=full_shape,
                endian=endian,
                block_offset=block_offset,
            )
            compress_time = time.perf_counter() - started
            if receiver_decompress_profile and meta["pipeline_name"] in receiver_decompress_profile:
                rate = float(receiver_decompress_profile[meta["pipeline_name"]])
                if rate <= 0:
                    raise ValueError(f"receiver profile for {meta['pipeline_name']} must be positive")
                decompress_time = len(raw) / rate
                decompress_source = "configured_receiver_profile"
            elif bandwidth_Bps:
                from .dedup_manifest import decompress_cpss_blob

                decompress_started = time.perf_counter()
                decompress_cpss_blob(blob)
                decompress_time = time.perf_counter() - decompress_started
                decompress_source = "local_receiver_measurement"
            else:
                decompress_time = 0.0
                decompress_source = "not_profiled"
            size = len(blob)
            row = {
                "pipeline_name": meta["pipeline_name"],
                "compressed_size": size,
                "compressed_bytes": size,
                "compress_time_s": compress_time,
                "decompress_time_s": decompress_time,
                "decompress_time_source": decompress_source,
                "status": "ok",
                "error": "",
            }
            if bandwidth_Bps:
                row["estimated_system_cost_s"] = system_cost_seconds(
                    compress_time, size, decompress_time, bandwidth_Bps
                )
            ranking.append(row)
            is_better = (
                best_size is None or size < best_size
                if not bandwidth_Bps
                else best_meta is None or row["estimated_system_cost_s"] < best_meta.get("estimated_system_cost_s", float("inf"))
            )
            if is_better:
                best_size = size
                best_blob = blob
                best_meta = meta
                if bandwidth_Bps:
                    best_meta = dict(best_meta)
                    best_meta["estimated_system_cost_s"] = row["estimated_system_cost_s"]
        except Exception as exc:
            ranking.append({
                "pipeline_name": canonical_pipeline_name(pipeline),
                "compressed_size": None,
                "compress_time_s": time.perf_counter() - started,
                "decompress_time_s": None,
                "status": "failed",
                "error": str(exc),
            })
    if best_blob is None or best_meta is None:
        raise RuntimeError("native exhaustive found no deployable pipeline for this block")
    best_meta = dict(best_meta)
    best_meta["selector_mode"] = "exhaustive"
    return best_blob, best_meta, ranking


def calibration_payloads(
    root: Path,
    files: list[FileRecord],
    blocks: list[UniqueBlockRecord],
    indices: list[int],
) -> list[tuple[int, int, bytes]]:
    file_map = {item.file_id: item for item in files}
    payloads: list[tuple[int, int, bytes]] = []
    for index in indices:
        if index < 0 or index >= len(blocks):
            raise IndexError(index)
        item = blocks[index]
        payloads.append((index, item_offset(files, item), source_block(root, file_map, item)))
    return payloads


def item_offset(files: list[FileRecord], item: UniqueBlockRecord) -> int:
    file_map = {row.file_id: row for row in files}
    return int(file_map[item.source_file_id].blocks[item.source_block_index].offset)


def choose_pipeline_from_trials(
    trials: list[dict[str, Any]],
    *,
    bandwidth_Bps: float | None = None,
) -> dict[str, Any]:
    totals: dict[str, dict[str, Any]] = {}
    expected_blocks = {row["sample_block_index"] for row in trials}
    for row in trials:
        pipeline = row["pipeline_name"]
        entry = totals.setdefault(
            pipeline,
            {
                "pipeline_name": pipeline,
                "ok_blocks": 0,
                "failed_blocks": 0,
                "total_compressed_size": 0,
                "total_compress_time_s": 0.0,
                "total_decompress_time_s": 0.0,
                "estimated_system_cost_s": 0.0,
            },
        )
        if row["status"] == "ok":
            entry["ok_blocks"] += 1
            entry["total_compressed_size"] += int(row["compressed_size"])
            entry["total_compress_time_s"] += float(row["compress_time_s"])
            entry["total_decompress_time_s"] += float(row.get("decompress_time_s") or 0.0)
        else:
            entry["failed_blocks"] += 1
    eligible = [
        entry for entry in totals.values()
        if entry["ok_blocks"] == len(expected_blocks) and entry["failed_blocks"] == 0
    ]
    if not eligible:
        raise RuntimeError("calibration found no pipeline that compressed every sampled block")
    if bandwidth_Bps is None:
        # Compatibility for old callers/results. New calibration always passes
        # an explicit bandwidth and therefore uses system-time ranking.
        selected = min(eligible, key=lambda item: (
            int(item["total_compressed_size"]),
            float(item["total_compress_time_s"]),
            str(item["pipeline_name"]),
        ))
    else:
        for item in eligible:
            item["estimated_system_cost_s"] = system_cost_seconds(
                item["total_compress_time_s"],
                item["total_compressed_size"],
                item["total_decompress_time_s"],
                bandwidth_Bps,
            )
        selected = min(eligible, key=lambda item: (
            float(item["estimated_system_cost_s"]),
            str(item["pipeline_name"]),
        ))
    selected["eligible_pipeline_count"] = len(eligible)
    return selected


def calibrate_fixed_pipeline(
    root: Path,
    files: list[FileRecord],
    blocks: list[UniqueBlockRecord],
    *,
    sampling_mode: str,
    candidates: list[str],
    sample_count: int = DEFAULT_CALIBRATION_BLOCKS,
    seed: int | None = None,
    bandwidth_Bps: float | None = None,
    receiver_decompress_profile: dict[str, float] | None = None,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    decision_memory: DecisionMemory | None = None,
) -> dict[str, Any]:
    if sampling_mode == "fixed_sampling":
        indices = fixed_sample_indices(len(blocks), sample_count)
    elif sampling_mode == "random_sampling":
        indices = random_sample_indices(len(blocks), seed=PRIMARY_RANDOM_SEED if seed is None else seed, count=sample_count)
    else:
        raise ValueError(f"unknown sampling mode: {sampling_mode}")

    started = time.perf_counter()
    trials: list[dict[str, Any]] = []
    memory_hits = 0
    memory_exact_hits = 0
    memory_similar_hits = 0
    memory_misses = 0
    candidate_pipeline_calls = 0
    memory_errors: list[str] = []
    canonical_candidates = tuple(canonical_pipeline_name(item) for item in candidates)
    runtime_signature = _decision_memory_runtime_signature()
    for block_index, block_offset, raw in calibration_payloads(root, files, blocks, indices):
        memory_key = None
        if decision_memory is not None:
            objective = "system_time" if bandwidth_Bps is not None and float(bandwidth_Bps) > 0 else "stored_size"
            memory_key = DecisionKey(
                block_hash=hashlib.sha256(raw).hexdigest(),
                raw_size=len(raw),
                context={
                    "dtype_name": dtype_name,
                    "full_shape": list(full_shape) if full_shape is not None else None,
                    "endian": endian,
                    "block_offset": block_offset,
                    "scenario": "science",
                    "source_group": "cpss_sampling_dedup_matrix",
                    "bandwidth_Bps": float(bandwidth_Bps) if bandwidth_Bps is not None else None,
                    "receiver_decompress_profile": receiver_decompress_profile or {},
                },
                candidates=canonical_candidates,
                objective=objective,
                runtime_signature=runtime_signature,
            )
            try:
                remembered = decision_memory.lookup(memory_key)
            except Exception as exc:
                remembered = None
                memory_errors.append(f"lookup block {block_index}: {exc}")
            if remembered is not None and canonical_pipeline_name(remembered.selected_pipeline) in canonical_candidates and remembered.ranking:
                trials.extend({
                    **cached_row,
                    "sampling_mode": sampling_mode,
                    "seed": seed,
                    "sample_block_index": block_index,
                    "raw_size": len(raw),
                } for cached_row in remembered.ranking)
                memory_hits += 1
                memory_exact_hits += 1
                continue

            similarity_features = extract_decision_features(raw)
            similarity_bucket = decision_feature_bucket(similarity_features)
            try:
                similar = decision_memory.lookup_similar(
                    memory_key,
                    similarity_bucket=similarity_bucket,
                    similarity_features=similarity_features,
                )
            except Exception as exc:
                similar = None
                memory_errors.append(f"similar lookup block {block_index}: {exc}")
            if similar is not None and similar.ranking:
                trials.extend({
                    **cached_row,
                    "sampling_mode": sampling_mode,
                    "seed": seed,
                    "sample_block_index": block_index,
                    "raw_size": len(raw),
                    "decision_source": "similar_memory",
                } for cached_row in similar.ranking)
                memory_hits += 1
                memory_similar_hits += 1
                try:
                    decision_memory.record(
                        memory_key,
                        selected_pipeline=similar.selected_pipeline,
                        ranking=similar.ranking,
                        similarity_bucket=similarity_bucket,
                        similarity_features=similarity_features,
                    )
                except Exception as exc:
                    memory_errors.append(f"write similar decision block {block_index}: {exc}")
                continue
            memory_misses += 1
        else:
            similarity_features = None
            similarity_bucket = None
        block_trials: list[dict[str, Any]] = []
        for pipeline in candidates:
            trial_started = time.perf_counter()
            candidate_pipeline_calls += 1
            try:
                blob, meta = compress_fixed_pipeline_blob(
                    raw,
                    pipeline,
                    selector_mode="calibrated_fixed",
                    selector_metadata={
                        "mode": "calibration",
                        "sampling_mode": sampling_mode,
                        "seed": seed,
                        "sample_block_index": block_index,
                    },
                    dtype_name=dtype_name,
                    full_shape=full_shape,
                    endian=endian,
                    block_offset=block_offset,
                )
                compress_time = time.perf_counter() - trial_started
                decompress_started = time.perf_counter()
                if receiver_decompress_profile and meta["pipeline_name"] in receiver_decompress_profile:
                    receiver_rate = float(receiver_decompress_profile[meta["pipeline_name"]])
                    if receiver_rate <= 0:
                        raise ValueError(f"receiver profile for {meta['pipeline_name']} must be positive")
                    decompress_time = len(raw) / receiver_rate
                    decompress_source = "configured_receiver_profile"
                else:
                    from .dedup_manifest import decompress_cpss_blob

                    decompress_cpss_blob(blob)
                    decompress_time = time.perf_counter() - decompress_started
                    decompress_source = "local_receiver_measurement"
                compressed_bytes = len(blob)
                trial = {
                    "sampling_mode": sampling_mode,
                    "seed": seed,
                    "sample_block_index": block_index,
                    "raw_size": len(raw),
                    "pipeline_name": meta["pipeline_name"],
                    "compressed_size": compressed_bytes,
                    "compressed_bytes": compressed_bytes,
                    "compress_time_s": compress_time,
                    "decompress_time_s": decompress_time,
                    "decompress_time_source": decompress_source,
                    "estimated_system_cost_s": (
                        system_cost_seconds(
                            compress_time,
                            compressed_bytes,
                            decompress_time,
                            bandwidth_Bps,
                        ) if bandwidth_Bps else ""
                    ),
                    "status": "ok",
                    "error": "",
                }
            except Exception as exc:
                trial = {
                    "sampling_mode": sampling_mode,
                    "seed": seed,
                    "sample_block_index": block_index,
                    "raw_size": len(raw),
                    "pipeline_name": canonical_pipeline_name(pipeline),
                    "compressed_size": None,
                    "compress_time_s": time.perf_counter() - trial_started,
                    "status": "failed",
                    "error": str(exc),
                }
            trials.append(trial)
            block_trials.append(trial)
        if decision_memory is not None and memory_key is not None and block_trials:
            eligible_block_trials = [row for row in block_trials if row["status"] == "ok"]
            if eligible_block_trials:
                if bandwidth_Bps is not None and float(bandwidth_Bps) > 0:
                    block_selected = min(
                        eligible_block_trials,
                        key=lambda row: (float(row.get("estimated_system_cost_s") or float("inf")), str(row["pipeline_name"])),
                    )
                else:
                    block_selected = min(
                        eligible_block_trials,
                        key=lambda row: (int(row["compressed_size"]), float(row["compress_time_s"]), str(row["pipeline_name"])),
                    )
                try:
                    decision_memory.record(
                        memory_key,
                        selected_pipeline=str(block_selected["pipeline_name"]),
                        ranking=[{
                            key: value for key, value in row.items()
                            if key not in {"sampling_mode", "seed", "sample_block_index", "raw_size"}
                        } for row in block_trials],
                        similarity_bucket=similarity_bucket,
                        similarity_features=similarity_features,
                    )
                except Exception as exc:
                    memory_errors.append(f"write block {block_index}: {exc}")
    selected = choose_pipeline_from_trials(trials, bandwidth_Bps=bandwidth_Bps) if trials else {
        "pipeline_name": None,
        "total_compressed_size": 0,
        "total_compress_time_s": 0.0,
        "eligible_pipeline_count": 0,
    }
    return {
        "sampling_mode": sampling_mode,
        "seed": seed,
        "sample_count": len(indices),
        "sample_block_indices": indices,
        "selected_pipeline": selected["pipeline_name"],
        "selected_total_compressed_size": selected["total_compressed_size"],
        "selected_total_compress_time_s": selected["total_compress_time_s"],
        "selected_total_decompress_time_s": selected.get("total_decompress_time_s", 0.0),
        "selected_estimated_system_cost_s": selected.get("estimated_system_cost_s", ""),
        "bandwidth_Bps": bandwidth_Bps,
        "receiver_decompress_profile_source": "configured" if receiver_decompress_profile else "local_measurement",
        "eligible_pipeline_count": selected["eligible_pipeline_count"],
        "calibration_time_s": time.perf_counter() - started,
        "decision_memory_hits": memory_hits,
        "decision_memory_exact_hits": memory_exact_hits,
        "decision_memory_similar_hits": memory_similar_hits,
        "decision_memory_misses": memory_misses,
        "candidate_pipeline_calls": candidate_pipeline_calls,
        "decision_memory_errors": memory_errors,
        "trials": trials,
    }


def evaluate_full_data_global_system_oracle(
    root: Path,
    files: list[FileRecord],
    blocks: list[UniqueBlockRecord],
    *,
    candidates: list[str],
    bandwidth_Bps: float,
    receiver_decompress_profile: dict[str, float] | None = None,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
) -> dict[str, Any]:
    """Evaluate one global pipeline on every block and select by system time."""
    if bandwidth_Bps <= 0:
        raise ValueError("full-data global oracle requires positive bandwidth_Bps")
    totals: list[dict[str, Any]] = []
    for pipeline in candidates:
        total_compress = 0.0
        total_decompress = 0.0
        total_bytes = 0
        status = "ok"
        error = ""
        for item in blocks:
            raw = source_block(root, {row.file_id: row for row in files}, item)
            started = time.perf_counter()
            try:
                blob, meta = compress_fixed_pipeline_blob(
                    raw,
                    pipeline,
                    selector_mode="full_data_global_oracle",
                    selector_metadata={"mode": "full_data_global_oracle", "candidate_count": len(candidates)},
                    dtype_name=dtype_name,
                    full_shape=full_shape,
                    endian=endian,
                    block_offset=item_offset(files, item),
                )
                compress_time = time.perf_counter() - started
                if receiver_decompress_profile and meta["pipeline_name"] in receiver_decompress_profile:
                    rate = float(receiver_decompress_profile[meta["pipeline_name"]])
                    if rate <= 0:
                        raise ValueError(f"receiver profile for {meta['pipeline_name']} must be positive")
                    decompress_time = len(raw) / rate
                else:
                    from .dedup_manifest import decompress_cpss_blob

                    decompress_started = time.perf_counter()
                    decompress_cpss_blob(blob)
                    decompress_time = time.perf_counter() - decompress_started
                total_compress += compress_time
                total_decompress += decompress_time
                total_bytes += len(blob)
            except Exception as exc:
                status = "failed"
                error = str(exc)
                break
        if status == "ok":
            totals.append({
                "pipeline_name": canonical_pipeline_name(pipeline),
                "compress_time_s": total_compress,
                "decompress_time_s": total_decompress,
                "compressed_bytes": total_bytes,
                "estimated_system_cost_s": system_cost_seconds(
                    total_compress, total_bytes, total_decompress, bandwidth_Bps
                ),
                "status": status,
                "error": error,
            })
        else:
            totals.append({
                "pipeline_name": canonical_pipeline_name(pipeline),
                "status": status,
                "error": error,
            })
    eligible = [row for row in totals if row["status"] == "ok"]
    if not eligible:
        raise RuntimeError("full-data global oracle found no usable pipeline")
    selected = min(eligible, key=lambda row: (row["estimated_system_cost_s"], row["pipeline_name"]))
    return {
        "oracle_type": "full_data_global_system_oracle",
        "selected_pipeline": selected["pipeline_name"],
        "selected_e2e_estimate_s": selected["estimated_system_cost_s"],
        "bandwidth_Bps": bandwidth_Bps,
        "receiver_decompress_profile_source": "configured" if receiver_decompress_profile else "local_measurement",
        "candidate_totals": totals,
    }


def select_default_calibration(
    fixed_calibration: dict[str, Any],
    random_calibrations: list[dict[str, Any]],
    *,
    sampling_mode: str = DEFAULT_SAMPLING_MODE,
) -> dict[str, Any]:
    """Select the calibration used by the default sampled deployment path."""
    if sampling_mode == "fixed_sampling":
        return fixed_calibration
    if sampling_mode == "random_sampling":
        if not random_calibrations:
            raise ValueError("random_sampling requires at least one random calibration")
        return random_calibrations[0]
    raise ValueError(f"unknown default sampling mode: {sampling_mode}")


def make_fixed_block_compressor(
    pipeline_name: str,
    *,
    sampling_mode: str,
    seed: int | None,
    files: list[FileRecord] | None = None,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
) -> BlockCompressor:
    pipeline_name = canonical_pipeline_name(pipeline_name)

    def _compress(raw: bytes, _block: UniqueBlockRecord) -> tuple[bytes, dict[str, Any]]:
        offset = item_offset(files, _block) if files else 0
        return compress_fixed_pipeline_blob(
            raw,
            pipeline_name,
            selector_mode="calibrated_fixed",
            selector_metadata={
                "mode": "calibrated_fixed",
                "sampling_mode": sampling_mode,
                "seed": seed,
                "fixed_pipeline": pipeline_name,
            },
            dtype_name=dtype_name,
            full_shape=full_shape,
            endian=endian,
            block_offset=offset,
        )

    return _compress


def make_native_exhaustive_block_compressor(
    candidates: list[str],
    *,
    files: list[FileRecord] | None = None,
    dtype_name: str | None = None,
    full_shape: tuple[int, ...] | None = None,
    endian: str = "little",
    bandwidth_Bps: float | None = None,
    receiver_decompress_profile: dict[str, float] | None = None,
    decision_memory: DecisionMemory | None = None,
) -> BlockCompressor:
    canonical_candidates = tuple(canonical_pipeline_name(item) for item in candidates)
    runtime_signature = _decision_memory_runtime_signature()

    def _compress(raw: bytes, _block: UniqueBlockRecord) -> tuple[bytes, dict[str, Any]]:
        offset = item_offset(files, _block) if files else 0
        memory_key = None
        memory_issue = ""
        similarity_features = None
        similarity_bucket = None
        if decision_memory is not None:
            objective = "system_time" if bandwidth_Bps is not None and float(bandwidth_Bps) > 0 else "stored_size"
            memory_key = DecisionKey(
                block_hash=hashlib.sha256(raw).hexdigest(),
                raw_size=len(raw),
                context={
                    "dtype_name": dtype_name,
                    "full_shape": list(full_shape) if full_shape is not None else None,
                    "endian": endian,
                    "block_offset": offset,
                    "scenario": "science",
                    "source_group": "cpss_sampling_dedup_matrix",
                    "bandwidth_Bps": float(bandwidth_Bps) if bandwidth_Bps is not None else None,
                    "receiver_decompress_profile": receiver_decompress_profile or {},
                },
                candidates=canonical_candidates,
                objective=objective,
                runtime_signature=runtime_signature,
            )
            try:
                remembered = decision_memory.lookup(memory_key)
            except Exception as exc:
                remembered = None
                memory_issue = f"lookup: {exc}"
            if remembered is not None:
                selected = canonical_pipeline_name(remembered.selected_pipeline)
                if selected in canonical_candidates:
                    try:
                        blob, meta = compress_fixed_pipeline_blob(
                            raw,
                            selected,
                            selector_mode="exhaustive",
                            selector_metadata={
                                "mode": "exhaustive",
                                "candidate_count": len(candidates),
                                "decision_source": "exact_memory",
                            },
                            dtype_name=dtype_name,
                            full_shape=full_shape,
                            endian=endian,
                            block_offset=offset,
                        )
                        meta.update({
                            "selector_mode": "exhaustive",
                            "decision_source": "exact_memory",
                            "memory_hit": True,
                            "candidate_calls": 1,
                            "ranking": remembered.ranking,
                        })
                        if memory_issue:
                            meta["decision_memory_error"] = memory_issue
                        return blob, meta
                    except Exception as exc:
                        memory_issue = f"remembered pipeline failed ({selected}): {exc}"
            try:
                similarity_features = extract_decision_features(raw)
                similarity_bucket = decision_feature_bucket(similarity_features)
                similar = decision_memory.lookup_similar(
                    memory_key,
                    similarity_bucket=similarity_bucket,
                    similarity_features=similarity_features,
                )
            except Exception as exc:
                similar = None
                memory_issue = f"{memory_issue}; similar lookup: {exc}".strip("; ")
            if similar is not None:
                selected = canonical_pipeline_name(similar.selected_pipeline)
                if selected in canonical_candidates:
                    try:
                        blob, meta = compress_fixed_pipeline_blob(
                            raw,
                            selected,
                            selector_mode="exhaustive",
                            selector_metadata={
                                "mode": "exhaustive",
                                "candidate_count": len(candidates),
                                "decision_source": "similar_memory",
                            },
                            dtype_name=dtype_name,
                            full_shape=full_shape,
                            endian=endian,
                            block_offset=offset,
                        )
                        meta.update({
                            "selector_mode": "exhaustive",
                            "decision_source": "similar_memory",
                            "memory_hit": True,
                            "candidate_calls": 1,
                            "similar_memory_neighbors": similar.neighbor_count,
                            "similar_memory_agreement": similar.agreement,
                            "ranking": similar.ranking,
                        })
                        if memory_issue:
                            meta["decision_memory_error"] = memory_issue
                        try:
                            decision_memory.record(
                                memory_key,
                                selected_pipeline=selected,
                                ranking=similar.ranking,
                                similarity_bucket=similarity_bucket,
                                similarity_features=similarity_features,
                            )
                        except Exception as exc:
                            meta["decision_memory_write_error"] = str(exc)
                        return blob, meta
                    except Exception as exc:
                        memory_issue = f"{memory_issue}; similar pipeline failed ({selected}): {exc}".strip("; ")

        blob, meta, ranking = compress_exhaustive_blob(
            raw,
            candidates,
            dtype_name=dtype_name,
            full_shape=full_shape,
            endian=endian,
            block_offset=offset,
            bandwidth_Bps=bandwidth_Bps,
            receiver_decompress_profile=receiver_decompress_profile,
        )
        meta = dict(meta)
        meta["ranking"] = ranking
        meta["decision_source"] = "exhaustive"
        meta["memory_hit"] = False
        meta["candidate_calls"] = len(ranking)
        if memory_issue:
            meta["decision_memory_error"] = memory_issue
        if decision_memory is not None and memory_key is not None:
            try:
                decision_memory.record(
                    memory_key,
                    selected_pipeline=meta["pipeline_name"],
                    ranking=ranking,
                    similarity_bucket=similarity_bucket,
                    similarity_features=similarity_features,
                )
            except Exception as exc:
                meta["decision_memory_write_error"] = str(exc)
        return blob, meta

    return _compress


def clone_records(
    files: list[FileRecord],
    blocks: list[UniqueBlockRecord],
) -> tuple[list[FileRecord], list[UniqueBlockRecord]]:
    return copy.deepcopy(files), copy.deepcopy(blocks)


def compress_records_to_bundle(
    *,
    root: Path,
    bundle: Path,
    run_id: str,
    block_size_bytes: int,
    files: list[FileRecord],
    unique_blocks: list[UniqueBlockRecord],
    stats: dict[str, Any],
    dedup_enabled: bool,
    compression_strategy: str,
    block_compressor: BlockCompressor,
    created_at: str,
    calibration: dict[str, Any] | None = None,
    data_metadata: dict[str, Any] | None = None,
    timeout_s: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "blocks").mkdir(parents=True, exist_ok=True)
    file_map = {item.file_id: item for item in files}
    started = time.perf_counter()
    total_compress = 0.0
    block_rows: list[dict[str, Any]] = []
    for order, item in enumerate(unique_blocks):
        if timeout_s and time.perf_counter() - started > timeout_s:
            raise TimeoutError(f"{compression_strategy} exceeded timeout {timeout_s}s before block {order}")
        raw = source_block(root, file_map, item)
        block_started = time.perf_counter()
        blob, metadata = block_compressor(raw, item)
        elapsed = time.perf_counter() - block_started
        total_compress += elapsed
        filename = f"{item.block_hash}.cpss" if dedup_enabled else f"{item.unique_block_id}-{item.block_hash}.cpss"
        cpss_path = bundle / "blocks" / filename
        cpss_path.write_bytes(blob)
        item.cpss_path = f"blocks/{filename}"
        item.compressed_size = len(blob)
        item.pipeline_name = metadata["pipeline_name"]
        item.pipeline_id = int(metadata["pipeline_id"])
        item.compress_time_s = elapsed
        item.cpss_sha256 = hashlib.sha256(blob).hexdigest()
        block_rows.append({
            "method": compression_strategy,
            "dedup_enabled": dedup_enabled,
            "unique_block_id": item.unique_block_id,
            "block_hash": item.block_hash,
            "raw_size": item.raw_size,
            "compressed_size": len(blob),
            "pipeline_name": item.pipeline_name,
            "pipeline_id": item.pipeline_id,
            "compress_time_s": elapsed,
            "selector_mode": metadata.get("selector_mode", ""),
            "decision_source": metadata.get("decision_source", ""),
            "memory_hit": bool(metadata.get("memory_hit", False)),
            "candidate_calls": int(metadata.get("candidate_calls", 0)),
            "similar_memory_neighbors": int(metadata.get("similar_memory_neighbors", 0)),
            "similar_memory_agreement": float(metadata.get("similar_memory_agreement", 0.0)),
            "decision_memory_error": metadata.get("decision_memory_error", ""),
            "decision_memory_write_error": metadata.get("decision_memory_write_error", ""),
        })
    compact_calibration = dict(calibration or {})
    compact_calibration.pop("trials", None)
    manifest_stats = dict(stats)
    manifest_stats.update({
        "dedup_enabled": bool(dedup_enabled),
        "compression_strategy": compression_strategy,
        "compressed_unique_bytes": sum(int(item.compressed_size or 0) for item in unique_blocks),
        "unique_block_count": len(unique_blocks),
        "compress_time_s": total_compress,
        "calibration": compact_calibration,
        "data_metadata": dict(data_metadata or {}),
    })
    manifest = build_manifest(
        run_id=run_id,
        source_root=root,
        block_size_bytes=block_size_bytes,
        files=files,
        unique_blocks=unique_blocks,
        stats=manifest_stats,
        created_at=created_at,
    )
    manifest_path = bundle / "manifest.json"
    write_manifest(manifest_path, manifest)
    manifest["stats"]["manifest_bytes"] = manifest_path.stat().st_size
    manifest["stats"]["transferred_bytes"] = (
        int(manifest["stats"]["manifest_bytes"])
        + int(manifest["stats"]["compressed_unique_bytes"])
    )
    write_manifest(manifest_path, manifest)
    return manifest, manifest["stats"], block_rows


def summarize_dedup_pairs(method_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in method_rows:
        strategy = row.get("cpss_strategy")
        dedup_mode = row.get("dedup_mode")
        if strategy and dedup_mode in {"dedup", "no_dedup"}:
            grouped[(row["dataset"], strategy)][dedup_mode] = row
    output = []
    for (dataset, strategy), pair in sorted(grouped.items()):
        no_dedup = pair.get("no_dedup")
        dedup = pair.get("dedup")
        if not no_dedup or not dedup:
            continue
        no_bytes = int(no_dedup.get("transferred_bytes") or 0)
        dedup_bytes = int(dedup.get("transferred_bytes") or 0)
        no_time = float(no_dedup.get("e2e_excluding_calibration_s") or 0.0)
        dedup_time = float(dedup.get("e2e_excluding_calibration_s") or 0.0)
        output.append({
            "dataset": dataset,
            "cpss_strategy": strategy,
            "no_dedup_transferred_bytes": no_bytes,
            "dedup_transferred_bytes": dedup_bytes,
            "transferred_bytes_saved": no_bytes - dedup_bytes,
            "transferred_bytes_saved_pct": (no_bytes - dedup_bytes) / no_bytes * 100.0 if no_bytes else 0.0,
            "no_dedup_e2e_s": no_time,
            "dedup_e2e_s": dedup_time,
            "e2e_saved_s": no_time - dedup_time,
            "e2e_gain_x": no_time / dedup_time if dedup_time else 0.0,
        })
    return output


def summarize_cpss_modes(method_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in method_rows:
        strategy = row.get("cpss_strategy")
        dedup_mode = row.get("dedup_mode")
        if strategy and dedup_mode:
            grouped[(row["dataset"], dedup_mode)][strategy] = row
    output = []
    for (dataset, dedup_mode), rows in sorted(grouped.items()):
        native = rows.get("native_exhaustive")
        if not native:
            continue
        native_ratio = float(native.get("compression_ratio_payload") or 0.0)
        native_time = float(native.get("compress_time_s") or 0.0)
        for strategy in ("fixed_sampling", "random_sampling"):
            item = rows.get(strategy)
            if not item:
                continue
            ratio = float(item.get("compression_ratio_payload") or 0.0)
            ctime = float(item.get("compress_time_s") or 0.0)
            output.append({
                "dataset": dataset,
                "dedup_mode": dedup_mode,
                "strategy": strategy,
                "native_compression_ratio": native_ratio,
                "strategy_compression_ratio": ratio,
                "compression_ratio_loss_pct": (native_ratio - ratio) / native_ratio * 100.0 if native_ratio else 0.0,
                "native_compress_time_s": native_time,
                "strategy_compress_time_s": ctime,
                "compress_time_saved_s": native_time - ctime,
                "compress_time_saved_pct": (native_time - ctime) / native_time * 100.0 if native_time else 0.0,
            })
    return output

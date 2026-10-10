#!/usr/bin/env python3
"""Legacy CPSS sampling/dedup GridFlux matrix experiment runner.

New local CPSS processing should use Script/run_cpss_pipeline.py. This script is
kept for historical GridFlux transport matrices until the remote control stage is
merged into the unified runner.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Compressor.cpss_sampling_matrix import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLING_MODE,
    PRIMARY_RANDOM_SEED,
    calibrate_fixed_pipeline,
    clone_records,
    compress_records_to_bundle,
    deployable_pipeline_names,
    evaluate_full_data_global_system_oracle,
    make_fixed_block_compressor,
    make_native_exhaustive_block_compressor,
    make_no_dedup_records,
    scan_dedup_records,
    select_default_calibration,
    summarize_cpss_modes,
    summarize_dedup_pairs,
)
from Compressor.dedup import sha256_file
from Compressor.decision_memory import DecisionMemory


DEFAULT_REMOTE_HOST = "47.116.174.181"
DEFAULT_REMOTE_ALIAS = "gridflux-beta-shanghai"
DEFAULT_REMOTE_ROOT = "/tmp/gridflux_cpss_sampling_dedup_matrix"
DEFAULT_REMOTE_PROJECT = "/root/projects/science-compressor"
DEFAULT_REMOTE_PYTHON = "/opt/gridflux-cpss-venv/bin/python3"
DEFAULT_GRIDFLUX_UPLOAD = "/root/projects/GridFlux-Beta/build/gridflux-tree-upload-client"
DEFAULT_GRIDFLUX_SERVER = "/root/projects/GridFlux-Beta/build/gridflux-gridftp-server"
DEFAULT_GRIDFLUX_PORT = 25252
DEFAULT_DATA_PORT_BASE = 20300
DEFAULT_BUCKET = "science-compressor-datasets"
DEFAULT_BLOCK_MIB = 8
DEFAULT_OBJECTS = {
    "alphafold_mmcif": "AI Dataset/ai_alphafold_mmcif_prepared_raw_sample.tar.zst",
    "rcsb_mmcif": "AI Dataset/ai_rcsb_mmcif_prepared_raw_sample.tar.zst",
}

METHOD_FIELDS = [
    "run_id", "dataset", "dataset_kind", "method", "cpss_strategy", "dedup_mode", "status", "error",
    "block_size_bytes", "file_count", "raw_bytes", "block_total_count", "block_unique_count",
    "block_duplicate_count", "file_dedup_hit_files", "file_dedup_saved_bytes", "block_dedup_saved_bytes",
    "unique_raw_bytes", "compressed_unique_bytes", "manifest_bytes", "transferred_bytes",
    "original_bytes", "selected_action", "predicted_raw_e2e", "predicted_compressed_e2e",
    "bypass_reason", "probe_time_s", "fallback_overhead_s", "bandwidth_Bps",
    "selected_pipeline", "sampling_mode", "seed", "calibration_block_indices", "calibration_time_s",
    "default_sampling_mode", "default_random_seed", "default_selected_pipeline",
    "scan_time_s", "compress_time_s", "gridflux_transfer_time_s", "remote_decompress_time_s",
    "remote_rebuild_time_s", "remote_verify_time_s", "transfer_wall_clock_s", "e2e_wall_clock_s",
    "output_ready_wall_clock_s", "verification_done_wall_clock_s", "cold_e2e_wall_clock_s",
    "warm_e2e_wall_clock_s", "transfer_goodput_Bps", "e2e_goodput_Bps", "e2e_speedup_vs_raw",
    "byte_reduction_ratio", "full_data_global_oracle_pipeline", "full_data_global_oracle_e2e_s",
    "system_regret_vs_full_data_global_oracle", "unsampled_block_system_regret",
    "per_block_exhaustive_oracle_role", "data_dtype", "data_shape", "data_endian",
    "data_metadata_source", "e2e_excluding_calibration_s", "e2e_including_calibration_s",
    "compression_ratio_payload", "logical_bandwidth_gain_vs_raw_bytes",
    "deprecated_logical_bandwidth_gain_vs_raw_bytes", "deprecated_fields",
    "raw_reference_e2e_s", "e2e_gain_vs_raw_excluding_calibration", "e2e_gain_vs_raw_including_calibration",
    "decision_memory_path", "decision_memory_hit_blocks", "decision_memory_miss_blocks",
    "decision_memory_exact_hits", "decision_memory_similar_hits", "candidate_pipeline_calls",
    "roundtrip_ok",
]
BLOCK_FIELDS = [
    "run_id", "dataset", "method", "dedup_mode", "unique_block_id", "block_hash", "raw_size",
    "compressed_size", "pipeline_name", "pipeline_id", "compress_time_s", "selector_mode",
    "decision_source", "memory_hit", "candidate_calls", "decision_memory_error",
    "decision_memory_write_error", "similar_memory_neighbors", "similar_memory_agreement",
]
CALIBRATION_FIELDS = [
    "run_id", "dataset", "sampling_mode", "seed", "sample_block_index", "pipeline_name",
    "raw_size", "compressed_size", "compressed_bytes", "compress_time_s", "decompress_time_s",
    "decompress_time_source", "estimated_system_cost_s", "status", "error",
]
DEDUP_COMPARE_FIELDS = [
    "dataset", "cpss_strategy", "no_dedup_transferred_bytes", "dedup_transferred_bytes",
    "transferred_bytes_saved", "transferred_bytes_saved_pct", "no_dedup_e2e_s", "dedup_e2e_s",
    "e2e_saved_s", "e2e_gain_x",
]
CPSS_COMPARE_FIELDS = [
    "dataset", "dedup_mode", "strategy", "native_compression_ratio", "strategy_compression_ratio",
    "compression_ratio_loss_pct", "native_compress_time_s", "strategy_compress_time_s",
    "compress_time_saved_s", "compress_time_saved_pct",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(value: object) -> str:
    return shlex.quote(str(value))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def run_cmd(
    command: list[str],
    *,
    cwd: Path = ROOT,
    log: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    rendered = "$ " + " ".join(q(item) for item in command)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=capture, check=False)
    if log is not None:
        with log.open("a", encoding="utf-8") as handle:
            if result.stdout:
                handle.write(result.stdout)
            if result.stderr:
                handle.write(result.stderr)
            handle.write(f"[exit {result.returncode}]\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {rendered}\n{result.stderr[-2000:]}")
    return result


def ssh_cmd(alias: str, command: str, *, log: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_cmd(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, command], log=log, check=check)


def serialize_expected_files(root: Path, output: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink()):
        rows.append({
            "rel_path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def parse_dataset_args(values: list[str]) -> list[tuple[str, Path, str]]:
    output = []
    for value in values:
        if "=" in value:
            name, path = value.split("=", 1)
        else:
            path = value
            name = Path(path).name
        output.append((name, Path(path).resolve(), "input_dir"))
    return output


def parse_seeds(value: str) -> list[int]:
    seeds = [int(item.strip()) for item in value.split(",") if item.strip()]
    return seeds or [PRIMARY_RANDOM_SEED]


def parse_shape(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    return tuple(int(item.strip()) for item in str(text).split(",") if item.strip())


def json_shape(shape: tuple[int, ...] | None) -> str:
    return json.dumps(list(shape or []))


def safe_div(numerator: float, denominator: float) -> float | str:
    return float(numerator) / float(denominator) if float(denominator) > 0 else ""


def predicted_from_calibration(calibration: dict[str, Any] | None, raw_bytes: int) -> float | str:
    if not calibration:
        return ""
    selected = calibration.get("selected_pipeline")
    selected_trials = [
        row for row in calibration.get("trials", [])
        if row.get("status") == "ok" and row.get("pipeline_name") == selected
    ]
    sampled_raw = sum(int(row.get("raw_size") or 0) for row in selected_trials)
    sampled_cost = sum(float(row.get("estimated_system_cost_s") or 0.0) for row in selected_trials)
    if sampled_raw <= 0 or sampled_cost <= 0:
        return ""
    return sampled_cost * int(raw_bytes) / sampled_raw


def decision_for_calibration(
    *,
    calibration: dict[str, Any] | None,
    raw_bytes: int,
    bandwidth_Bps: float | None,
    min_expected_speedup: float,
    guard_margin: float,
) -> dict[str, Any]:
    predicted_raw = safe_div(raw_bytes, bandwidth_Bps) if bandwidth_Bps else ""
    predicted_compressed = predicted_from_calibration(calibration, raw_bytes)
    decision = {
        "selected_action": "compress",
        "predicted_raw_e2e": predicted_raw,
        "predicted_compressed_e2e": predicted_compressed,
        "bypass_reason": "",
        "probe_time_s": float((calibration or {}).get("calibration_time_s") or 0.0),
        "fallback_overhead_s": 0.0,
    }
    if bandwidth_Bps is None or predicted_raw == "" or predicted_compressed == "":
        return decision
    threshold = float(predicted_raw) / max(float(min_expected_speedup), 1e-12)
    threshold *= 1.0 - max(0.0, float(guard_margin))
    if float(predicted_compressed) >= threshold:
        decision["selected_action"] = "raw_bypass"
        decision["bypass_reason"] = "predicted_compressed_e2e_not_better_than_raw_guard"
        decision["fallback_overhead_s"] = decision["probe_time_s"]
    return decision


def add_wall_metrics(row: dict[str, Any], *, raw_reference_e2e_s: float, raw_bytes: int, transferred: int) -> None:
    transfer_wall = float(row.get("gridflux_transfer_time_s") or 0.0)
    output_ready = float(row.get("output_ready_wall_clock_s") or row.get("e2e_excluding_calibration_s") or 0.0)
    cold = float(row.get("cold_e2e_wall_clock_s") or row.get("e2e_including_calibration_s") or output_ready)
    row.setdefault("original_bytes", raw_bytes)
    row["transfer_wall_clock_s"] = transfer_wall
    row["e2e_wall_clock_s"] = cold
    row.setdefault("output_ready_wall_clock_s", output_ready)
    row.setdefault("verification_done_wall_clock_s", cold)
    row.setdefault("cold_e2e_wall_clock_s", cold)
    row.setdefault("warm_e2e_wall_clock_s", output_ready)
    row["transfer_goodput_Bps"] = safe_div(raw_bytes, transfer_wall)
    row["e2e_goodput_Bps"] = safe_div(raw_bytes, cold)
    row["e2e_speedup_vs_raw"] = safe_div(raw_reference_e2e_s, cold)
    row["byte_reduction_ratio"] = safe_div(raw_bytes, transferred)
    row["deprecated_logical_bandwidth_gain_vs_raw_bytes"] = row.get("logical_bandwidth_gain_vs_raw_bytes", "")
    row["deprecated_fields"] = json.dumps([
        "logical_bandwidth_gain_vs_raw_bytes",
        "e2e_excluding_calibration_s",
        "e2e_including_calibration_s",
    ])


def load_dataset_metadata(path: Path | None, dataset: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("datasets", payload if isinstance(payload, list) else [])
    if not isinstance(rows, list):
        return {}
    return next((dict(row) for row in rows if str(row.get("slug", "")) == dataset), {})


class MatrixRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.default_sampling_mode = args.sampling_mode
        self.run_id = args.result_dir.name
        self.result_dir = args.result_dir.resolve()
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.command_log = self.result_dir / "command_log.txt"
        self.tmp = Path(tempfile.mkdtemp(prefix=f"{self.run_id}-", dir="/tmp"))
        self.remote_session = f"{args.remote_root.rstrip('/')}/{self.run_id}"
        self.server_root = f"{self.remote_session}/server_root"
        self.remote_log_root = f"{self.remote_session}/logs"
        self.remote_server_pid = f"{self.remote_session}/server.pid"
        self.remote_server_log = f"{self.remote_log_root}/gridflux-server.log"
        self.remote_event_log = f"{self.remote_log_root}/gridflux-server-events.jsonl"
        self.remote_started = False
        self.method_rows: list[dict[str, Any]] = []
        self.block_rows: list[dict[str, Any]] = []
        self.calibration_rows: list[dict[str, Any]] = []
        self.datasets: list[tuple[str, Path, str]] = []
        self.default_selected_pipeline = ""
        self.bandwidth_Bps: float | None = args.bandwidth_Bps
        self.decision_memory = DecisionMemory(args.decision_memory) if args.decision_memory else None

    def data_metadata(self, dataset: str) -> dict[str, Any]:
        row = load_dataset_metadata(self.args.datasets_metadata, dataset)
        dtype_name = self.args.dtype if self.args.dtype is not None else row.get("dtype")
        shape_value = self.args.shape if self.args.shape is not None else row.get("shape")
        shape = tuple(int(item) for item in shape_value) if shape_value else None
        endian = self.args.endian if self.args.endian != "little" else str(row.get("endian") or "little")
        return {
            "dtype_name": dtype_name,
            "shape": shape,
            "endian": endian if endian in {"little", "big"} else "little",
            "metadata_source": "cli" if self.args.dtype or self.args.shape else ("datasets.json" if row else "byte_stream_default"),
        }

    def sync_code(self) -> None:
        excludes = [
            "--exclude=Model/",
            "--exclude=Dataset/",
            "--exclude=RawDataset/",
            "--exclude=MediumDataset/",
            "--exclude=ExperimentRecords/",
            "--exclude=.pytest_cache/",
            "--exclude=__pycache__/",
            "--exclude=*.pyc",
            "--exclude=*.part",
            "--exclude=*.lock",
        ]
        run_cmd(["rsync", "-az", *excludes, f"{ROOT}/", f"{self.args.remote_alias}:{DEFAULT_REMOTE_PROJECT}/"], log=self.command_log)
        local_manifest = self.tmp / "code_manifest.json"
        rows = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(ROOT)
            if any(part in {"Model", "Dataset", "RawDataset", "MediumDataset", "ExperimentRecords", ".pytest_cache"} for part in rel.parts):
                continue
            rows.append({"path": rel.as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        local_manifest.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
        remote_manifest = f"{self.remote_session}/code_manifest.json"
        ssh_cmd(self.args.remote_alias, f"mkdir -p {q(self.remote_session)}", log=self.command_log)
        run_cmd(["rsync", "-az", str(local_manifest), f"{self.args.remote_alias}:{remote_manifest}"], log=self.command_log)
        result = ssh_cmd(
            self.args.remote_alias,
            f"""set -e
cd {q(DEFAULT_REMOTE_PROJECT)}
{q(DEFAULT_REMOTE_PYTHON)} - <<'PY'
import hashlib, json
from pathlib import Path
expected = json.loads(Path({json.dumps(remote_manifest)}).read_text())
actual = []
for p in sorted(Path('.').rglob('*')):
    if not p.is_file() or '__pycache__' in p.parts or p.suffix == '.pyc':
        continue
    r = p.relative_to('.')
    if any(x in {{'Model','Dataset','RawDataset','MediumDataset','ExperimentRecords','.pytest_cache'}} for x in r.parts):
        continue
    actual.append({{'path': r.as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size}})
print(json.dumps({{'match': sorted(actual, key=lambda x: x['path']) == sorted(expected, key=lambda x: x['path']), 'local_count': len(expected), 'remote_count': len(actual)}}))
PY
rm -f {q(remote_manifest)}
""",
            log=self.command_log,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        if not payload.get("match"):
            raise RuntimeError(f"remote code manifest mismatch: {payload}")

    def start_remote_server(self) -> None:
        command = f"""set -e
if ss -ltn | grep -q ':{self.args.gridflux_port} '; then
  echo "GridFlux port {self.args.gridflux_port} is already occupied" >&2
  exit 2
fi
rm -rf {q(self.remote_session)}
mkdir -p {q(self.server_root)} {q(self.remote_log_root)}
nohup {q(DEFAULT_GRIDFLUX_SERVER)} --host 0.0.0.0 --port {self.args.gridflux_port} --data-port-base {self.args.data_port_base} --root {q(self.server_root)} --connections {self.args.connections} --checksum {q(self.args.checksum)} --checksum-backend auto --event-log {q(self.remote_event_log)} > {q(self.remote_server_log)} 2>&1 < /dev/null &
server_pid="$!"
printf '%s\\n' "$server_pid" > {q(self.remote_server_pid)}
for i in $(seq 1 40); do
  if ss -ltn | grep -q ':{self.args.gridflux_port} '; then exit 0; fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat {q(self.remote_server_log)} || true
    exit 1
  fi
  sleep 0.25
done
cat {q(self.remote_server_log)} || true
exit 1
"""
        ssh_cmd(self.args.remote_alias, command, log=self.command_log)
        self.remote_started = True

    def stop_remote_server(self) -> None:
        command = f"""if [ -f {q(self.remote_server_pid)} ]; then
  server_pid="$(cat {q(self.remote_server_pid)} 2>/dev/null || true)"
  if [ -n "$server_pid" ]; then
    kill "$server_pid" 2>/dev/null || true
    for i in $(seq 1 20); do
      if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
      sleep 0.25
    done
    kill -9 "$server_pid" 2>/dev/null || true
  fi
fi
rm -rf {q(self.remote_session)}
"""
        ssh_cmd(self.args.remote_alias, command, log=self.command_log, check=False)
        self.remote_started = False

    def upload(self, source: Path, destination: str, tag: str) -> tuple[float, dict[str, Any]]:
        summary = self.result_dir / f"{tag}_gridflux_summary.json"
        event_log = self.result_dir / f"{tag}_gridflux_events.jsonl"
        command = [
            self.args.gridflux_tree_upload,
            "--host", self.args.remote_host,
            "--port", str(self.args.gridflux_port),
            "--source-dir", str(source),
            "--dest-dir", destination,
            "--connections", str(self.args.connections),
            "--file-parallelism", str(self.args.file_parallelism),
            "--checksum", self.args.checksum,
            "--checksum-backend", "auto",
            "--json-summary", str(summary),
            "--event-log", str(event_log),
        ]
        started = time.perf_counter()
        run_cmd(command, log=self.command_log)
        elapsed = time.perf_counter() - started
        return elapsed, json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}

    def remote_verify_raw(self, remote_raw: str, expected: Path, remote_output: str) -> dict[str, Any]:
        remote_expected = f"{self.remote_session}/expected-raw.json"
        run_cmd(["rsync", "-az", str(expected), f"{self.args.remote_alias}:{remote_expected}"], log=self.command_log)
        result_path = f"{self.remote_session}/verify-raw.json"
        result = ssh_cmd(
            self.args.remote_alias,
            (
                f"{q(DEFAULT_REMOTE_PYTHON)} {q(DEFAULT_REMOTE_PROJECT + '/Script/gridflux_cpss_dedup_remote.py')} "
                f"--mode raw --raw-dir {q(remote_raw)} --expected-json {q(remote_expected)} "
                f"--output-dir {q(remote_output)} --result-json {q(result_path)} "
                f"&& rm -f {q(result_path)} {q(remote_expected)}"
            ),
            log=self.command_log,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def remote_verify_bundle(self, remote_bundle: str, remote_output: str) -> dict[str, Any]:
        result_path = f"{self.remote_session}/verify-bundle.json"
        result = ssh_cmd(
            self.args.remote_alias,
            (
                f"{q(DEFAULT_REMOTE_PYTHON)} {q(DEFAULT_REMOTE_PROJECT + '/Script/gridflux_cpss_dedup_remote.py')} "
                f"--mode bundle --bundle-dir {q(remote_bundle)} --output-dir {q(remote_output)} "
                f"--result-json {q(result_path)} && rm -f {q(result_path)}"
            ),
            log=self.command_log,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    def prepare_oss_datasets(self) -> list[tuple[str, Path, str]]:
        output: list[tuple[str, Path, str]] = []
        if not self.args.run_default_oss:
            return output
        oss_dir = self.tmp / "oss"
        oss_dir.mkdir(parents=True, exist_ok=True)
        for name, object_key in DEFAULT_OBJECTS.items():
            dataset_dir = oss_dir / name
            archive = oss_dir / f"{name}.tar.zst"
            extracted = dataset_dir / "extracted"
            uri = f"oss://{self.args.oss_bucket}/{object_key}"
            stat_file = self.result_dir / f"{name}_oss_stat.txt"
            stat = run_cmd(["ossutil", "stat", uri], log=self.command_log, check=True)
            stat_file.write_text(stat.stdout + stat.stderr, encoding="utf-8")
            run_cmd(["ossutil", "cp", uri, str(archive)], log=self.command_log)
            run_cmd(["zstd", "-t", str(archive)], log=self.command_log)
            extracted.mkdir(parents=True, exist_ok=True)
            run_cmd(["tar", "--zstd", "-xf", str(archive), "-C", str(extracted)], log=self.command_log)
            output.append((name, extracted, "real_low_duplicate"))
        return output

    def construct_dedup_positive(self, source_root: Path, block_size: int) -> tuple[str, Path, str]:
        target = self.tmp / "dedup_positive"
        target.mkdir(parents=True, exist_ok=True)
        source_files = sorted(path for path in source_root.rglob("*") if path.is_file() and path.stat().st_size >= block_size * 3)
        if not source_files:
            raise RuntimeError("cannot construct dedup-positive dataset: no source file has three blocks")
        source = source_files[0]
        with source.open("rb") as handle:
            block_a = handle.read(block_size)
            block_b = handle.read(block_size)
            block_c = handle.read(block_size)
        if len(block_c) < block_size:
            block_c = bytes((byte ^ 0x5A) for byte in block_a)
        (target / "file_a.bin").write_bytes(block_a + block_b)
        (target / "file_b.bin").write_bytes(block_a + block_b)
        (target / "file_c.bin").write_bytes(block_a + block_c)
        (target / "file_empty.txt").write_bytes(b"")
        return ("dedup_positive_constructed", target, "dedup_positive")

    def load_datasets(self) -> None:
        datasets = parse_dataset_args(self.args.input_dir or [])
        datasets.extend(self.prepare_oss_datasets())
        if self.args.include_dedup_positive:
            if not datasets:
                raise RuntimeError("--include-dedup-positive requires at least one input or default OSS dataset")
            datasets.append(self.construct_dedup_positive(datasets[0][1], self.args.block_mib * 1024 * 1024))
        if not datasets:
            raise RuntimeError("no datasets selected")
        self.datasets = datasets

    def run_raw_direct(self, dataset: str, kind: str, root: Path, raw_bytes: int, raw_scan_time: float) -> dict[str, Any]:
        method = "raw_direct"
        method_tmp = self.tmp / dataset / method
        method_tmp.mkdir(parents=True, exist_ok=True)
        expected_path = method_tmp / "expected.json"
        expected = serialize_expected_files(root, expected_path)
        destination = f"{self.run_id}/{dataset}/{method}"
        started = time.perf_counter()
        transfer_time, _summary = self.upload(root, destination, f"{dataset}_{method}")
        verify = self.remote_verify_raw(
            f"{self.server_root}/{destination}",
            expected_path,
            f"{self.server_root}/rebuild/{self.run_id}/{dataset}/{method}",
        )
        e2e = time.perf_counter() - started
        row = {
            "run_id": self.run_id,
            "dataset": dataset,
            "dataset_kind": kind,
            "method": method,
            "cpss_strategy": "raw",
            "dedup_mode": "none",
            "status": "completed",
            "error": "",
            "block_size_bytes": self.args.block_mib * 1024 * 1024,
            "file_count": len(expected),
            "raw_bytes": raw_bytes,
            "original_bytes": raw_bytes,
            "unique_raw_bytes": raw_bytes,
            "transferred_bytes": raw_bytes,
            "selected_action": "raw_bypass",
            "predicted_raw_e2e": safe_div(raw_bytes, raw_bytes / transfer_time) if transfer_time > 0 else "",
            "predicted_compressed_e2e": "",
            "bypass_reason": "raw_reference",
            "probe_time_s": 0.0,
            "fallback_overhead_s": 0.0,
            "bandwidth_Bps": safe_div(raw_bytes, transfer_time),
            "scan_time_s": raw_scan_time,
            "gridflux_transfer_time_s": transfer_time,
            "remote_verify_time_s": verify.get("verify_time_s", 0.0),
            "transfer_wall_clock_s": transfer_time,
            "e2e_wall_clock_s": transfer_time,
            "output_ready_wall_clock_s": transfer_time,
            "verification_done_wall_clock_s": e2e,
            "cold_e2e_wall_clock_s": transfer_time,
            "warm_e2e_wall_clock_s": transfer_time,
            "e2e_excluding_calibration_s": transfer_time,
            "e2e_including_calibration_s": transfer_time,
            "raw_reference_e2e_s": transfer_time,
            "transfer_goodput_Bps": safe_div(raw_bytes, transfer_time),
            "e2e_goodput_Bps": safe_div(raw_bytes, transfer_time),
            "e2e_speedup_vs_raw": 1.0,
            "byte_reduction_ratio": 1.0,
            "logical_bandwidth_gain_vs_raw_bytes": 1.0,
            "deprecated_logical_bandwidth_gain_vs_raw_bytes": 1.0,
            "deprecated_fields": json.dumps(["logical_bandwidth_gain_vs_raw_bytes", "e2e_excluding_calibration_s", "e2e_including_calibration_s"]),
            "default_sampling_mode": self.default_sampling_mode,
            "default_random_seed": self.args.random_seeds[0],
            "roundtrip_ok": bool(verify.get("ok")),
        }
        self.method_rows.append(row)
        return row

    def run_cpss_method(
        self,
        *,
        dataset: str,
        kind: str,
        root: Path,
        raw_reference_e2e_s: float,
        files: Any,
        blocks: Any,
        stats: dict[str, Any],
        scan_time_s: float,
        cpss_strategy: str,
        dedup_mode: str,
        block_compressor: Any,
        calibration: dict[str, Any] | None,
        decision: dict[str, Any] | None = None,
        data_metadata: dict[str, Any] | None = None,
    ) -> None:
        method = f"cpss_{cpss_strategy}_{dedup_mode}"
        method_tmp = self.tmp / dataset / method
        bundle = method_tmp / "bundle"
        destination = f"{self.run_id}/{dataset}/{method}"
        method_started = time.perf_counter()
        row_base = {
            "run_id": self.run_id,
            "dataset": dataset,
            "dataset_kind": kind,
            "method": method,
            "cpss_strategy": cpss_strategy,
            "dedup_mode": dedup_mode,
            "block_size_bytes": self.args.block_mib * 1024 * 1024,
            "selected_pipeline": (calibration or {}).get("selected_pipeline", ""),
            "sampling_mode": (calibration or {}).get("sampling_mode", ""),
            "seed": (calibration or {}).get("seed", ""),
            "calibration_block_indices": json.dumps((calibration or {}).get("sample_block_indices", [])),
            "calibration_time_s": float((calibration or {}).get("calibration_time_s", 0.0)),
            "default_sampling_mode": self.default_sampling_mode,
            "default_random_seed": self.args.random_seeds[0],
            "default_selected_pipeline": self.default_selected_pipeline,
            "scan_time_s": scan_time_s,
            "raw_reference_e2e_s": raw_reference_e2e_s,
            "original_bytes": int(stats["raw_bytes"]),
            "selected_action": (decision or {}).get("selected_action", "compress"),
            "predicted_raw_e2e": (decision or {}).get("predicted_raw_e2e", ""),
            "predicted_compressed_e2e": (decision or {}).get("predicted_compressed_e2e", ""),
            "bypass_reason": (decision or {}).get("bypass_reason", ""),
            "probe_time_s": float((decision or {}).get("probe_time_s", 0.0)),
            "fallback_overhead_s": float((decision or {}).get("fallback_overhead_s", 0.0)),
            "bandwidth_Bps": self.bandwidth_Bps,
            "data_dtype": (data_metadata or {}).get("dtype_name") or "",
            "data_shape": json_shape((data_metadata or {}).get("shape")),
            "data_endian": (data_metadata or {}).get("endian", ""),
            "data_metadata_source": (data_metadata or {}).get("metadata_source", ""),
            "decision_memory_path": str(self.args.decision_memory) if self.args.decision_memory else "",
        }
        try:
            if (decision or {}).get("selected_action") == "raw_bypass":
                expected_path = method_tmp / "expected.json"
                serialize_expected_files(root, expected_path)
                transfer_started = time.perf_counter()
                transfer_time, _summary = self.upload(
                    root,
                    destination,
                    f"{dataset}_{method}_raw_bypass",
                )
                verify = self.remote_verify_raw(
                    f"{self.server_root}/{destination}",
                    expected_path,
                    f"{self.server_root}/rebuild/{self.run_id}/{dataset}/{method}_raw_bypass",
                )
                verification_done = time.perf_counter() - transfer_started
                output_ready = transfer_time
                row = {
                    **row_base,
                    "status": "completed",
                    "error": "",
                    "file_count": stats.get("file_count", 0),
                    "raw_bytes": int(stats["raw_bytes"]),
                    "unique_raw_bytes": int(stats.get("unique_raw_bytes", stats["raw_bytes"])),
                    "transferred_bytes": int(stats["raw_bytes"]),
                    "compressed_unique_bytes": 0,
                    "manifest_bytes": 0,
                    "gridflux_transfer_time_s": transfer_time,
                    "remote_verify_time_s": float(verify.get("verify_time_s", 0.0)),
                    "output_ready_wall_clock_s": output_ready,
                    "verification_done_wall_clock_s": verification_done,
                    "cold_e2e_wall_clock_s": output_ready + float(row_base["probe_time_s"]),
                    "warm_e2e_wall_clock_s": output_ready,
                    "e2e_excluding_calibration_s": output_ready,
                    "e2e_including_calibration_s": output_ready + float(row_base["probe_time_s"]),
                    "compression_ratio_payload": "",
                    "logical_bandwidth_gain_vs_raw_bytes": 1.0,
                    "roundtrip_ok": bool(verify.get("ok")),
                }
                add_wall_metrics(
                    row,
                    raw_reference_e2e_s=raw_reference_e2e_s,
                    raw_bytes=int(stats["raw_bytes"]),
                    transferred=int(stats["raw_bytes"]),
                )
                self.method_rows.append(row)
                return
            c_files, c_blocks = clone_records(files, blocks)
            manifest, manifest_stats, block_rows = compress_records_to_bundle(
                root=root,
                bundle=bundle,
                run_id=self.run_id,
                block_size_bytes=self.args.block_mib * 1024 * 1024,
                files=c_files,
                unique_blocks=c_blocks,
                stats=stats,
                dedup_enabled=dedup_mode == "dedup",
                compression_strategy=cpss_strategy,
                block_compressor=block_compressor,
                created_at=utc_now(),
                calibration=calibration,
                data_metadata=data_metadata,
                timeout_s=self.args.native_exhaustive_timeout_s if cpss_strategy == "native_exhaustive" else None,
            )
            memory_active = self.decision_memory is not None and cpss_strategy == "native_exhaustive"
            memory_hits = sum(bool(item.get("memory_hit")) for item in block_rows)
            memory_misses = sum(item.get("decision_source") == "exhaustive" for item in block_rows) if memory_active else 0
            candidate_pipeline_calls = sum(int(item.get("candidate_calls", 0)) for item in block_rows)
            transfer_time, _summary = self.upload(bundle, destination, f"{dataset}_{method}")
            verify = self.remote_verify_bundle(
                f"{self.server_root}/{destination}",
                f"{self.server_root}/rebuild/{self.run_id}/{dataset}/{method}",
            )
            verification_done = time.perf_counter() - method_started
            verify_only = max(
                0.0,
                float(verify.get("verify_time_s", 0.0))
                - float(verify.get("decompress_time_s", 0.0))
                - float(verify.get("rebuild_time_s", 0.0)),
            )
            output_ready = max(0.0, verification_done - verify_only)
            e2e_excl = output_ready
            e2e_incl = output_ready + float(row_base["calibration_time_s"])
            raw_bytes = int(stats["raw_bytes"])
            compressed = int(manifest_stats["compressed_unique_bytes"])
            transferred = int(manifest_stats["transferred_bytes"])
            row = {
                **row_base,
                "status": "completed",
                "error": "",
                "file_count": stats.get("file_count", 0),
                "raw_bytes": raw_bytes,
                "block_total_count": stats.get("block_total_count", 0),
                "block_unique_count": stats.get("block_unique_count", 0),
                "block_duplicate_count": stats.get("block_duplicate_count", 0),
                "file_dedup_hit_files": stats.get("duplicate_file_count", 0),
                "file_dedup_saved_bytes": stats.get("file_dedup_saved_bytes", 0),
                "block_dedup_saved_bytes": stats.get("block_dedup_saved_bytes", 0),
                "unique_raw_bytes": stats.get("unique_raw_bytes", raw_bytes),
                "compressed_unique_bytes": compressed,
                "manifest_bytes": int(manifest_stats["manifest_bytes"]),
                "transferred_bytes": transferred,
                "compress_time_s": float(manifest_stats["compress_time_s"]),
                "decision_memory_hit_blocks": memory_hits,
                "decision_memory_miss_blocks": memory_misses,
                "decision_memory_exact_hits": sum(item.get("decision_source") == "exact_memory" for item in block_rows),
                "decision_memory_similar_hits": sum(item.get("decision_source") == "similar_memory" for item in block_rows),
                "candidate_pipeline_calls": candidate_pipeline_calls,
                "gridflux_transfer_time_s": transfer_time,
                "remote_decompress_time_s": float(verify.get("decompress_time_s", 0.0)),
                "remote_rebuild_time_s": float(verify.get("rebuild_time_s", 0.0)),
                "remote_verify_time_s": float(verify.get("verify_time_s", 0.0)),
                "transfer_wall_clock_s": transfer_time,
                "output_ready_wall_clock_s": output_ready,
                "verification_done_wall_clock_s": verification_done,
                "cold_e2e_wall_clock_s": e2e_incl,
                "warm_e2e_wall_clock_s": e2e_excl,
                "e2e_wall_clock_s": e2e_incl,
                "e2e_excluding_calibration_s": e2e_excl,
                "e2e_including_calibration_s": e2e_incl,
                "compression_ratio_payload": raw_bytes / compressed if compressed else 0.0,
                "logical_bandwidth_gain_vs_raw_bytes": raw_bytes / transferred if transferred else 0.0,
                "e2e_gain_vs_raw_excluding_calibration": raw_reference_e2e_s / e2e_excl if e2e_excl else 0.0,
                "e2e_gain_vs_raw_including_calibration": raw_reference_e2e_s / e2e_incl if e2e_incl else 0.0,
                "roundtrip_ok": bool(verify.get("ok")),
            }
            add_wall_metrics(
                row,
                raw_reference_e2e_s=raw_reference_e2e_s,
                raw_bytes=raw_bytes,
                transferred=transferred,
            )
            for block_row in block_rows:
                block_row.update({"run_id": self.run_id, "dataset": dataset, "dedup_mode": dedup_mode})
                block_row["method"] = method
            self.block_rows.extend(block_rows)
            (self.result_dir / f"{dataset}_{method}_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except TimeoutError as exc:
            row = {
                **row_base,
                "status": "partial_oracle_timeout" if cpss_strategy == "native_exhaustive" else "failed",
                "error": str(exc),
                "e2e_excluding_calibration_s": time.perf_counter() - method_started,
                "e2e_including_calibration_s": time.perf_counter() - method_started + float(row_base["calibration_time_s"]),
            }
        except Exception as exc:
            row = {**row_base, "status": "failed", "error": str(exc)}
        self.method_rows.append(row)

    def run_dataset(self, dataset: str, root: Path, kind: str) -> None:
        block_size = self.args.block_mib * 1024 * 1024
        data_metadata = self.data_metadata(dataset)
        no_scan_started = time.perf_counter()
        no_files, no_blocks, no_stats = make_no_dedup_records(root, block_size)
        no_scan_time = time.perf_counter() - no_scan_started
        dedup_scan_started = time.perf_counter()
        dedup_files, dedup_blocks, dedup_stats = scan_dedup_records(root, block_size)
        dedup_scan_time = time.perf_counter() - dedup_scan_started
        raw_row = self.run_raw_direct(dataset, kind, root, int(no_stats["raw_bytes"]), no_scan_time)

        candidates = self.args.pipelines or deployable_pipeline_names()
        measured_bandwidth = safe_div(
            int(no_stats["raw_bytes"]),
            float(raw_row.get("gridflux_transfer_time_s") or 0.0),
        )
        self.bandwidth_Bps = self.args.bandwidth_Bps or (
            float(measured_bandwidth) if measured_bandwidth != "" else None
        )
        fixed_cal = calibrate_fixed_pipeline(
            root,
            no_files,
            no_blocks,
            sampling_mode="fixed_sampling",
            candidates=candidates,
            sample_count=self.args.calibration_blocks,
            bandwidth_Bps=self.bandwidth_Bps,
            receiver_decompress_profile=None,
            dtype_name=data_metadata.get("dtype_name"),
            full_shape=data_metadata.get("shape"),
            endian=data_metadata.get("endian") or "little",
        )
        random_calibrations = [
            calibrate_fixed_pipeline(
                root,
                no_files,
                no_blocks,
                sampling_mode="random_sampling",
                candidates=candidates,
                sample_count=self.args.calibration_blocks,
                seed=seed,
                bandwidth_Bps=self.bandwidth_Bps,
                receiver_decompress_profile=None,
                dtype_name=data_metadata.get("dtype_name"),
                full_shape=data_metadata.get("shape"),
                endian=data_metadata.get("endian") or "little",
            )
            for seed in self.args.random_seeds
        ]
        for cal in [fixed_cal, *random_calibrations]:
            for trial in cal["trials"]:
                self.calibration_rows.append({"run_id": self.run_id, "dataset": dataset, **trial})
        primary_random_cal = random_calibrations[0]
        self.default_selected_pipeline = select_default_calibration(
            fixed_cal,
            random_calibrations,
            sampling_mode=self.default_sampling_mode,
        )["selected_pipeline"]
        decisions = {
            "fixed_sampling": decision_for_calibration(
                calibration=fixed_cal,
                raw_bytes=int(no_stats["raw_bytes"]),
                bandwidth_Bps=self.bandwidth_Bps,
                min_expected_speedup=self.args.min_expected_speedup,
                guard_margin=self.args.guard_margin,
            ),
            "random_sampling": decision_for_calibration(
                calibration=primary_random_cal,
                raw_bytes=int(no_stats["raw_bytes"]),
                bandwidth_Bps=self.bandwidth_Bps,
                min_expected_speedup=self.args.min_expected_speedup,
                guard_margin=self.args.guard_margin,
            ),
        }
        strategies = [
            ("native_exhaustive", None, make_native_exhaustive_block_compressor(
                candidates,
                files=no_files,
                dtype_name=data_metadata.get("dtype_name"),
                full_shape=data_metadata.get("shape"),
                endian=data_metadata.get("endian") or "little",
                bandwidth_Bps=self.bandwidth_Bps,
                decision_memory=self.decision_memory,
            )),
            ("fixed_sampling", fixed_cal, make_fixed_block_compressor(
                fixed_cal["selected_pipeline"],
                sampling_mode="fixed_sampling",
                seed=None,
                files=no_files,
                dtype_name=data_metadata.get("dtype_name"),
                full_shape=data_metadata.get("shape"),
                endian=data_metadata.get("endian") or "little",
            )),
            ("random_sampling", primary_random_cal, make_fixed_block_compressor(
                primary_random_cal["selected_pipeline"],
                sampling_mode="random_sampling",
                seed=primary_random_cal["seed"],
                files=no_files,
                dtype_name=data_metadata.get("dtype_name"),
                full_shape=data_metadata.get("shape"),
                endian=data_metadata.get("endian") or "little",
            )),
        ]
        strategies = [item for item in strategies if item[0] in self.args.strategies]
        for strategy, calibration, compressor in strategies:
            self.run_cpss_method(
                dataset=dataset,
                kind=kind,
                root=root,
                raw_reference_e2e_s=float(raw_row["e2e_excluding_calibration_s"]),
                files=no_files,
                blocks=no_blocks,
                stats=no_stats,
                scan_time_s=no_scan_time,
                cpss_strategy=strategy,
                dedup_mode="no_dedup",
                block_compressor=compressor,
                calibration=calibration,
                decision=decisions.get(strategy),
                data_metadata=data_metadata,
            )
            self.run_cpss_method(
                dataset=dataset,
                kind=kind,
                root=root,
                raw_reference_e2e_s=float(raw_row["e2e_excluding_calibration_s"]),
                files=dedup_files,
                blocks=dedup_blocks,
                stats=dedup_stats,
                scan_time_s=dedup_scan_time,
                cpss_strategy=strategy,
                dedup_mode="dedup",
                block_compressor=compressor,
                calibration=calibration,
                decision=decisions.get(strategy),
                data_metadata=data_metadata,
            )

    def write_outputs(self) -> None:
        dedup_rows = summarize_dedup_pairs(self.method_rows)
        cpss_rows = summarize_cpss_modes(self.method_rows)
        write_csv(self.result_dir / "method_metrics.csv", self.method_rows, METHOD_FIELDS)
        write_csv(self.result_dir / "block_metrics.csv", self.block_rows, BLOCK_FIELDS)
        write_csv(self.result_dir / "calibration.csv", self.calibration_rows, CALIBRATION_FIELDS)
        write_csv(self.result_dir / "dedup_comparison.csv", dedup_rows, DEDUP_COMPARE_FIELDS)
        write_csv(self.result_dir / "cpss_mode_comparison.csv", cpss_rows, CPSS_COMPARE_FIELDS)
        summary = {
            "run_id": self.run_id,
            "created_at": utc_now(),
            "datasets": [name for name, _root, _kind in self.datasets],
            "strategies": self.args.strategies,
            "default_sampling_mode": self.default_sampling_mode,
            "default_random_seed": self.args.random_seeds[0],
            "method_rows": self.method_rows,
            "dedup_comparison": dedup_rows,
            "cpss_mode_comparison": cpss_rows,
        }
        (self.result_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.write_summary_md(dedup_rows, cpss_rows)

    def write_summary_md(self, dedup_rows: list[dict[str, Any]], cpss_rows: list[dict[str, Any]]) -> None:
        lines = [
            "# CPSS 采样策略与去重流水线矩阵实验报告",
            "",
            f"- run_id: `{self.run_id}`",
            f"- block size: `{self.args.block_mib} MiB`",
            f"- datasets: `{', '.join(name for name, _root, _kind in self.datasets)}`",
            f"- default sampling mode: `{self.default_sampling_mode}`",
            f"- default random seed: `{self.args.random_seeds[0]}`",
            "",
            "## 方法状态",
            "",
            "| dataset | method | status | selected pipeline | transferred bytes | e2e excl calibration | e2e incl calibration |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in self.method_rows:
            lines.append(
                f"| `{row.get('dataset')}` | `{row.get('method')}` | `{row.get('status')}` | "
                f"`{row.get('selected_pipeline', '')}` | `{row.get('transferred_bytes', '')}` | "
                f"`{row.get('e2e_excluding_calibration_s', '')}` | `{row.get('e2e_including_calibration_s', '')}` |"
            )
        lines.extend(["", "## 去重收益", "", "| dataset | strategy | bytes saved | bytes saved pct | e2e gain |", "|---|---:|---:|---:|---:|"])
        for row in dedup_rows:
            lines.append(
                f"| `{row['dataset']}` | `{row['cpss_strategy']}` | `{row['transferred_bytes_saved']}` | "
                f"`{row['transferred_bytes_saved_pct']:.6f}` | `{row['e2e_gain_x']:.6f}` |"
            )
        lines.extend(["", "## 采样相对原生 exhaustive", "", "| dataset | dedup | strategy | ratio loss pct | compress time saved pct |", "|---|---:|---:|---:|---:|"])
        for row in cpss_rows:
            lines.append(
                f"| `{row['dataset']}` | `{row['dedup_mode']}` | `{row['strategy']}` | "
                f"`{row['compression_ratio_loss_pct']:.6f}` | `{row['compress_time_saved_pct']:.6f}` |"
            )
        lines.extend([
            "",
            "## 说明",
            "",
            "- `native_exhaustive` 是 CPSS 原生最高压缩比 oracle，每个正式 block 遍历候选管线；它不是生产快路径。",
            "- `fixed_sampling` 和 `random_sampling` 的 calibration block 只用于选固定管线；报告同时给出包含和不包含 calibration 的端到端口径。",
            "- `dedup` 和 `no_dedup` 在同一 CPSS 策略下比较，用于拆分去重收益和压缩收益。",
            "- 如果 native exhaustive 超时，状态为 `partial_oracle_timeout`，不把采样方法替代为 oracle。",
        ])
        (self.result_dir / "summary_zh.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def cleanup(self) -> None:
        if self.remote_started:
            self.stop_remote_server()
        else:
            ssh_cmd(self.args.remote_alias, f"rm -rf {q(self.remote_session)}", log=self.command_log, check=False)
        if not self.args.keep_temp:
            shutil.rmtree(self.tmp, ignore_errors=True)
        remote_exists = ssh_cmd(self.args.remote_alias, f"test -e {q(self.remote_session)}", log=self.command_log, check=False).returncode == 0
        (self.result_dir / "cleanup_audit.json").write_text(
            json.dumps({
                "local_tmp": str(self.tmp),
                "local_tmp_exists": self.tmp.exists(),
                "remote_session": self.remote_session,
                "remote_session_exists": remote_exists,
                "keep_temp": bool(self.args.keep_temp),
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def execute(self) -> None:
        try:
            self.load_datasets()
            self.sync_code()
            self.start_remote_server()
            for dataset, root, kind in self.datasets:
                self.run_dataset(dataset, root, kind)
            self.write_outputs()
        finally:
            try:
                self.cleanup()
            finally:
                if self.decision_memory is not None:
                    self.decision_memory.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", action="append", default=[], help="Dataset path, or name=/path. Can repeat.")
    parser.add_argument("--run-default-oss", action="store_true")
    parser.add_argument("--include-dedup-positive", action="store_true")
    parser.add_argument("--oss-bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-alias", default=DEFAULT_REMOTE_ALIAS)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--gridflux-tree-upload", default=DEFAULT_GRIDFLUX_UPLOAD)
    parser.add_argument("--gridflux-port", type=int, default=DEFAULT_GRIDFLUX_PORT)
    parser.add_argument("--data-port-base", type=int, default=DEFAULT_DATA_PORT_BASE)
    parser.add_argument("--connections", type=int, default=2)
    parser.add_argument("--file-parallelism", type=int, default=2)
    parser.add_argument("--checksum", default="crc32c")
    parser.add_argument("--block-mib", type=int, choices=[1, 4, 8, 16, 32], default=DEFAULT_BLOCK_MIB)
    parser.add_argument("--calibration-blocks", type=int, default=5)
    parser.add_argument("--dtype", type=str, default=None)
    parser.add_argument("--shape", type=parse_shape, default=None)
    parser.add_argument("--endian", choices=["little", "big"], default="little")
    parser.add_argument("--datasets-metadata", type=Path, default=None)
    parser.add_argument("--bandwidth-Bps", dest="bandwidth_Bps", type=float, default=None)
    parser.add_argument("--min-expected-speedup", type=float, default=1.0)
    parser.add_argument("--guard-margin", type=float, default=0.0)
    parser.add_argument(
        "--sampling-mode",
        choices=["fixed_sampling", "random_sampling"],
        default=DEFAULT_SAMPLING_MODE,
        help="Default calibrated sampling mode; the full matrix still runs fixed and random diagnostics.",
    )
    parser.add_argument(
        "--random-seeds",
        default=f"{DEFAULT_RANDOM_SEED},20260728,20260729",
        help="Comma-separated random calibration seeds; the first seed is the default random seed.",
    )
    parser.add_argument(
        "--strategies",
        default="native_exhaustive,fixed_sampling,random_sampling",
        help="Comma-separated CPSS strategies to execute after raw; default runs the full matrix.",
    )
    parser.add_argument("--pipelines", default=None, help="Comma-separated deployable pipeline whitelist.")
    parser.add_argument("--native-exhaustive-timeout-s", type=float, default=0.0)
    parser.add_argument(
        "--decision-memory",
        type=Path,
        default=None,
        help="Optional persistent SQLite path for exact block-to-pipeline decisions (native_exhaustive only).",
    )
    parser.add_argument("--result-dir", type=Path, default=None)
    parser.add_argument("--keep-temp", action="store_true")
    args = parser.parse_args()
    args.random_seeds = parse_seeds(args.random_seeds)
    args.pipelines = [item.strip() for item in args.pipelines.split(",") if item.strip()] if args.pipelines else None
    valid_strategies = {"native_exhaustive", "fixed_sampling", "random_sampling"}
    args.strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    if not args.strategies or any(item not in valid_strategies for item in args.strategies):
        parser.error(f"--strategies must contain only: {','.join(sorted(valid_strategies))}")
    if args.native_exhaustive_timeout_s <= 0:
        args.native_exhaustive_timeout_s = None
    if args.result_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.result_dir = ROOT / "ExperimentRecords" / f"cpss_sampling_dedup_matrix_{stamp}"
    return args


def main() -> int:
    args = parse_args()
    runner = MatrixRunner(args)
    try:
        runner.execute()
        return 0
    except Exception as exc:
        runner.result_dir.mkdir(parents=True, exist_ok=True)
        (runner.result_dir / "blocker_report.md").write_text(
            f"# Blocker Report\n\n- status: `failed`\n- error: `{exc}`\n",
            encoding="utf-8",
        )
        (runner.result_dir / "summary.json").write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if runner.remote_started:
            runner.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

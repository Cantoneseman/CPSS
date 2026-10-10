#!/usr/bin/env python3
"""Run a small, measured DCC bandwidth-sensitivity experiment.

This runner creates a deterministic prefix-balanced subset for each selected
dataset, then invokes the existing final GridFlux runner at the requested
bandwidth so CPSS makes a fresh bandwidth-aware decision. A prepared subset
can be reused to compare bandwidths on identical input bytes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shlex
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = ROOT / "DCCData" / "full-v1" / "datasets"
DEFAULT_DATASETS = ("cesm_atm", "miranda", "noaa_gsod_csv")
DEFAULT_METHODS = "raw,zstd-3,cpss-adaptive"
DEFAULT_MAX_MIB = 128
DEFAULT_REPEATS = 3
DEFAULT_BANDWIDTH_MBPS = 10.0
DEFAULT_RUNNER_PYTHON = Path("/opt/gridflux-cpss-venv/bin/python3")
DEFAULT_REMOTE_HOST = "47.116.174.181"
DEFAULT_REMOTE_PYTHON = "/opt/gridflux-cpss-venv/bin/python3"
DEFAULT_GRIDFLUX_PORT = 25252
DEFAULT_DATA_PORT_BASE = 20300
DEFAULT_ALIGNMENT = {
    "cesm_atm": 4,
    "miranda": 8,
    "noaa_gsod_csv": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def bandwidth_label(bandwidth_mbps: float) -> str:
    return f"{bandwidth_mbps:g}".replace(".", "p")


def copy_prefix(source: Path, destination: Path, count: int) -> str:
    """Copy exactly count bytes and return the output SHA-256."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    remaining = count
    with source.open("rb") as src, destination.open("wb") as dst:
        while remaining:
            chunk = src.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                raise IOError(f"short read while sampling {source}")
            dst.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def allocate_prefix_bytes(sizes: list[int], target: int, alignment: int) -> list[int]:
    """Allocate a deterministic, proportional prefix budget across files."""
    if not sizes or target <= 0:
        return [0 for _ in sizes]
    if alignment <= 0:
        raise ValueError("alignment must be positive")
    total = sum(sizes)
    target = min(target, total)
    target -= target % alignment
    if target <= 0:
        return [0 for _ in sizes]

    allocations = [
        min(size, int(target * size / total) // alignment * alignment)
        for size in sizes
    ]
    remaining = target - sum(allocations)
    while remaining >= alignment:
        progressed = False
        for index, size in enumerate(sizes):
            if allocations[index] + alignment <= size and remaining >= alignment:
                allocations[index] += alignment
                remaining -= alignment
                progressed = True
        if not progressed:
            break
    return allocations


def build_dataset_subset(
    source_root: Path,
    subset_root: Path,
    dataset: str,
    max_bytes: int,
    alignment: int,
) -> dict[str, Any]:
    source = source_root / dataset / "raw"
    destination = subset_root / dataset / "raw"
    if not source.is_dir():
        raise FileNotFoundError(f"dataset raw directory does not exist: {source}")
    files = sorted(path for path in source.rglob("*") if path.is_file() and not path.is_symlink())
    if not files:
        raise ValueError(f"dataset has no regular files: {source}")
    total = sum(path.stat().st_size for path in files)
    target = min(max_bytes, total)
    allocations = allocate_prefix_bytes([path.stat().st_size for path in files], target, alignment)
    records: list[dict[str, Any]] = []
    for path, count in zip(files, allocations):
        if count <= 0:
            continue
        relative = path.relative_to(source).as_posix()
        output = destination / relative
        output_hash = copy_prefix(path, output, count)
        records.append({
            "relative_path": relative,
            "source_bytes": path.stat().st_size,
            "sampled_bytes": count,
            "prefix_sha256": output_hash,
        })
    sampled = sum(item["sampled_bytes"] for item in records)
    if sampled <= 0:
        raise ValueError(f"subset budget produced no bytes for {dataset}")
    return {
        "dataset": dataset,
        "source_dir": str(source),
        "subset_dir": str(destination),
        "source_bytes": total,
        "sampled_bytes": sampled,
        "source_file_count": len(files),
        "sampled_file_count": len(records),
        "max_bytes": max_bytes,
        "alignment": alignment,
        "files": records,
    }


def verify_dataset_subset(record: dict[str, Any]) -> None:
    subset_dir = Path(record["subset_dir"])
    expected_paths = set()
    for item in record["files"]:
        path = subset_dir / item["relative_path"]
        expected_paths.add(path.resolve())
        if not path.is_file() or path.is_symlink():
            raise SystemExit(f"prepared subset file is missing or not regular: {path}")
        if path.stat().st_size != item["sampled_bytes"]:
            raise SystemExit(f"prepared subset file has the wrong size: {path}")
        if sha256_file(path) != item["prefix_sha256"]:
            raise SystemExit(f"prepared subset file failed SHA-256 verification: {path}")
    actual_paths = {
        path.resolve()
        for path in subset_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != expected_paths:
        raise SystemExit(f"prepared subset file list differs from its manifest: {subset_dir}")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def summarize_run(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    rows = load_csv(run_dir / "method_metrics.csv")
    valid = [
        row for row in rows
        if row.get("status") == "completed"
        and row.get("roundtrip_ok", "True").lower() == "true"
    ]
    by_pair: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    for row in valid:
        by_pair[(row.get("dataset", ""), row.get("repeat", ""))][row.get("method", "")] = row
    speedups: dict[str, list[float]] = defaultdict(list)
    for (dataset, _repeat), pair in by_pair.items():
        raw = pair.get("raw")
        cpss = pair.get("cpss-adaptive")
        if not raw or not cpss:
            continue
        raw_time = float(raw.get("T_ready_s") or 0.0)
        cpss_time = float(cpss.get("T_ready_s") or 0.0)
        if raw_time > 0 and cpss_time > 0:
            speedups[dataset].append(raw_time / cpss_time)
    raw_rates = []
    for row in valid:
        if row.get("method") != "raw":
            continue
        transfer_s = float(row.get("gridflux_transfer_time_s") or 0.0)
        raw_bytes = float(row.get("raw_bytes") or 0.0)
        if transfer_s > 0 and raw_bytes > 0:
            raw_rates.append(raw_bytes * 8.0 / transfer_s / 1_000_000.0)
    cpss_rows = [row for row in valid if row.get("method") == "cpss-adaptive"]
    return {
        "status": "completed" if rows and len(valid) == len(rows) else "completed_with_failures",
        "configured_bandwidth_mbps": config["bandwidth_mbps"],
        "rows": len(rows),
        "valid_rows": len(valid),
        "failed_rows": len(rows) - len(valid),
        "selected_actions": dict(Counter(row.get("selected_action", "") for row in cpss_rows)),
        "selected_pipelines": dict(Counter(row.get("selected_pipeline", "") for row in cpss_rows)),
        "median_raw_observed_mbps": sorted(raw_rates)[len(raw_rates) // 2] if raw_rates else None,
        "cpss_speedup_vs_raw_by_dataset": {
            dataset: {
                "repeats": len(values),
                "median_speedup": sorted(values)[len(values) // 2],
            }
            for dataset, values in sorted(speedups.items())
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    runner_python_default = DEFAULT_RUNNER_PYTHON if DEFAULT_RUNNER_PYTHON.is_file() else Path(sys.executable)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--datasets", default=",".join(DEFAULT_DATASETS))
    parser.add_argument(
        "--max-mib",
        type=int,
        default=DEFAULT_MAX_MIB,
        help="maximum input bytes copied per dataset (default 128 MiB; three default datasets total 384 MiB)",
    )
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--bandwidth-mbps", type=float, default=DEFAULT_BANDWIDTH_MBPS)
    parser.add_argument("--methods", default=DEFAULT_METHODS)
    parser.add_argument("--probe-candidates", default="fast")
    parser.add_argument("--adaptive-max-samples", type=int, choices=[1, 2], default=2)
    parser.add_argument("--adaptive-stability-cv", type=float, default=0.20)
    parser.add_argument("--random-seed", type=int, default=20260727)
    parser.add_argument("--remote-alias", default="gridflux-beta-shanghai")
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-python", default=DEFAULT_REMOTE_PYTHON)
    parser.add_argument("--gridflux-port", type=int, default=DEFAULT_GRIDFLUX_PORT)
    parser.add_argument("--data-port-base", type=int, default=DEFAULT_DATA_PORT_BASE)
    parser.add_argument("--runner-python", type=Path, default=runner_python_default)
    parser.add_argument("--result-dir", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--reuse-subset-from",
        type=Path,
        default=None,
        help="reuse and verify input_subset from a prepared run directory",
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    datasets = [item.strip() for item in args.datasets.split(",") if item.strip()]
    if not datasets or len(datasets) != len(set(datasets)):
        raise SystemExit("datasets must contain one or more unique dataset names")
    unknown = sorted(set(datasets) - set(DEFAULT_ALIGNMENT))
    if unknown:
        raise SystemExit(f"unsupported subset dataset(s): {', '.join(unknown)}")
    if args.max_mib <= 0 or args.repeats <= 0 or args.bandwidth_mbps <= 0:
        raise SystemExit("max-mib, repeats, and bandwidth-mbps must be positive")
    source_root = args.source_root.expanduser().resolve()
    if not args.runner_python.is_file():
        raise SystemExit(f"runner Python interpreter does not exist: {args.runner_python}")
    rate_label = bandwidth_label(args.bandwidth_mbps)
    result_root = (
        args.result_dir
        or ROOT / "ExperimentRecords" / f"dcc_{rate_label}mbps_subset_{utc_stamp()}"
    ).resolve()
    run_dir = result_root / "run"
    result_root.mkdir(parents=True, exist_ok=True)
    manifest_path = result_root / "subset_manifest.json"
    subset_source_dir: Path | None = None
    if args.reuse_subset_from:
        reuse_root = args.reuse_subset_from.expanduser().resolve()
        source_manifest_path = reuse_root / "subset_manifest.json"
        if not source_manifest_path.is_file():
            raise SystemExit(f"subset manifest not found: {source_manifest_path}")
        source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        expected = {
            "source_root": str(source_root),
            "max_mib": args.max_mib,
            "datasets": datasets,
        }
        if any(source_manifest.get(key) != value for key, value in expected.items()):
            raise SystemExit(
                "reused subset configuration differs from the requested source, size, or datasets"
            )
        records_by_dataset = {
            record.get("dataset"): record
            for record in source_manifest.get("records", [])
        }
        if set(records_by_dataset) != set(datasets):
            raise SystemExit("reused subset manifest does not contain exactly the requested datasets")
        subset_source_dir = reuse_root / "input_subset"
        for dataset in datasets:
            record = records_by_dataset[dataset]
            expected_dir = (subset_source_dir / dataset / "raw").resolve()
            if Path(record.get("subset_dir", "")).resolve() != expected_dir:
                raise SystemExit(f"subset manifest points outside the reusable subset: {dataset}")
            verify_dataset_subset(record)
        manifest = dict(source_manifest)
        manifest["status"] = "reused"
        manifest["source_manifest"] = str(source_manifest_path)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected = {
            "source_root": str(source_root),
            "max_mib": args.max_mib,
            "datasets": datasets,
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise SystemExit(f"existing subset manifest does not match requested configuration: {result_root}")
        for record in manifest.get("records", []):
            verify_dataset_subset(record)
    else:
        subset_root = result_root / "input_subset"
        if subset_root.exists() and any(subset_root.iterdir()):
            raise SystemExit(f"subset directory exists without a manifest: {subset_root}")
        dataset_records = [
            build_dataset_subset(
                source_root,
                subset_root,
                dataset,
                args.max_mib * 1024 * 1024,
                DEFAULT_ALIGNMENT[dataset],
            )
            for dataset in datasets
        ]
        manifest = {
            "status": "prepared",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_root": str(source_root),
            "max_mib": args.max_mib,
            "datasets": datasets,
            "records": dataset_records,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    bandwidth_bits = int(round(args.bandwidth_mbps * 1_000_000))
    run_id = args.run_id or result_root.name
    dataset_args = []
    for dataset in datasets:
        input_root = subset_source_dir or (result_root / "input_subset")
        dataset_args.extend(["--dataset", f"{dataset}={input_root / dataset / 'raw'}"])
    command = [
        str(args.runner_python),
        str(ROOT / "Script" / "run_dcc_final_gridflux.py"),
        *dataset_args,
        "--methods", args.methods,
        "--repeats", str(args.repeats),
        "--high-repeats", str(args.repeats),
        "--high-repeat-datasets", "",
        "--bandwidth-bps", str(bandwidth_bits),
        "--block-mib", "128",
        "--adaptive-max-samples", str(args.adaptive_max_samples),
        "--adaptive-stability-cv", str(args.adaptive_stability_cv),
        "--probe-candidates", args.probe_candidates,
        "--random-seed", str(args.random_seed),
        "--remote-alias", args.remote_alias,
        "--remote-host", args.remote_host,
        "--remote-python", args.remote_python,
        "--gridflux-port", str(args.gridflux_port),
        "--data-port-base", str(args.data_port_base),
        "--run-id", run_id,
        "--result-dir", str(run_dir),
    ]
    config = {
        "status": "prepared",
        "source_root": str(source_root),
        "subset_manifest": str(manifest_path),
        "result_dir": str(result_root),
        "run_dir": str(run_dir),
        "subset_source_dir": str(subset_source_dir or (result_root / "input_subset")),
        "datasets": datasets,
        "max_mib_per_dataset": args.max_mib,
        "methods": [item.strip() for item in args.methods.split(",") if item.strip()],
        "repeats": args.repeats,
        "bandwidth_mbps": args.bandwidth_mbps,
        "bandwidth_bps": bandwidth_bits,
        "cpss_reselection": True,
        "runner_python": str(args.runner_python),
        "remote_alias": args.remote_alias,
        "remote_host": args.remote_host,
        "remote_python": args.remote_python,
        "gridflux_port": args.gridflux_port,
        "data_port_base": args.data_port_base,
        "runner_command": command,
    }
    (result_root / "experiment_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    (result_root / "runner_command.sh").write_text("#!/bin/sh\nset -eu\n" + " ".join(shlex.quote(str(item)) for item in command) + "\n", encoding="utf-8")
    print(json.dumps({"subset_manifest": str(manifest_path), "command": command, "result_dir": str(result_root)}, ensure_ascii=False, indent=2, default=str))
    if args.dry_run or args.prepare_only:
        return 0
    if run_dir.exists() and any(run_dir.iterdir()):
        raise SystemExit(f"run directory already contains files; choose a new --result-dir: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = result_root / "runner_stdout.log"
    stderr_path = result_root / "runner_stderr.log"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(command, cwd=str(ROOT), stdout=stdout, stderr=stderr, text=True, check=False)
    summary = summarize_run(run_dir, config) if (run_dir / "method_metrics.csv").exists() else {
        "status": "runner_failed",
        "returncode": completed.returncode,
    }
    config["status"] = summary.get("status", "runner_failed")
    config["runner_returncode"] = completed.returncode
    (result_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (result_root / "experiment_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

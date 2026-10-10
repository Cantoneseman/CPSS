#!/usr/bin/env python3
"""Finish the DCC repair run and launch the requested supplementary tests.

This script is intentionally resumable.  It first waits for the repaired main
run, retries every non-successful row through the frozen runner, and writes a
canonical 306-row CSV with repaired rows replacing failed rows.  It then runs
the representative ablation, reuses that GridFlux matrix for the GridFlux
side of the transport cross-check, runs the matching scp measurements, and
produces the 10/100/1000 Mbps post-hoc sensitivity model.

The script is designed to run detached on the Shenzhen controller.  Every
phase writes its own log and status file so a later invocation can resume
without deleting prior evidence.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[1]
PYTHON = Path("/opt/gridflux-cpss-venv/bin/python3")
REMOTE_ALIAS = "gridflux-beta-shanghai"
DATA_ROOT = PROJECT / "DCCData/full-v1/datasets"
SOURCE_RUN = PROJECT / "ExperimentRecords/dcc_main_v1_20260927_repaired_20260929"
RETRY_RUN = PROJECT / "ExperimentRecords/dcc_main_v1_20260927_nyx_retry_20260929"
FINAL_RUN = PROJECT / "ExperimentRecords/dcc_main_v1_20260927_final_20260929"
WORK_ROOT = PROJECT / "ExperimentRecords/dcc_remaining_supplementary_20260929"
STATUS_PATH = WORK_ROOT / "status.json"
CANONICAL_RUN_ID = "dcc_main_v1_20260927_final_20260929"
DATASETS = (
    "cesm_atm",
    "miranda",
    "noaa_gsod_csv",
)
METHODS = ("raw", "zstd-3", "cpss-adaptive")
REPEATS = 3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def key(row: dict[str, Any]) -> tuple[str, int, str]:
    return str(row["dataset"]), int(row["repeat"]), str(row["method"])


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def command_log_path(name: str) -> Path:
    return WORK_ROOT / "logs" / f"{name}.log"


def run_command(
    command: list[str],
    *,
    name: str,
    cwd: Path = PROJECT,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    log = command_log_path(name)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    rendered = " ".join(shlex.quote(str(item)) for item in command)
    try:
        result = subprocess.run(
            [str(item) for item in command],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        result = subprocess.CompletedProcess(command, 124, stdout, f"{stderr}\nTIMEOUT")
    log.write_text(
        f"started={started}\n$ {rendered}\nreturncode={result.returncode}\n"
        f"[stdout]\n{result.stdout}\n[stderr]\n{result.stderr}\n",
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({name}, rc={result.returncode}): {rendered}\n{result.stderr[-3000:]}")
    return result


def update_status(phase: str, status: str, **extra: Any) -> None:
    payload = {
        "run_id": WORK_ROOT.name,
        "updated_at_utc": utc_now(),
        "phase": phase,
        "status": status,
        **extra,
    }
    write_json(STATUS_PATH, payload)


def wait_for_main_run() -> dict[str, Any]:
    update_status("wait_main", "running", source_run=str(SOURCE_RUN))
    status_path = SOURCE_RUN / "run_status.json"
    while not status_path.exists():
        time.sleep(30)
    while True:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("status") != "running":
            return status
        time.sleep(30)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: Iterable[str] | None = None) -> None:
    rows = list(rows)
    if fieldnames is None:
        fields: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for name in row:
                if name not in seen:
                    fields.append(name)
                    seen.add(name)
        fieldnames = fields
    fields = list(fieldnames)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({name: row.get(name, "") for name in fields} for row in rows)


def retry_failed_main_rows() -> Path:
    update_status("retry_main_failures", "running")
    if not (RETRY_RUN / "run_status.json").exists():
        command = [
            str(PYTHON),
            "Script/run_dcc_final_gridflux.py",
            "--dataset-root",
            str(DATA_ROOT),
            "--resume-from",
            str(SOURCE_RUN),
            "--run-id",
            RETRY_RUN.name,
            "--result-dir",
            str(RETRY_RUN),
            "--methods",
            "raw,gzip-6,lz4,zstd-3,xz-6,cpss-adaptive",
            "--repeats",
            "3",
            "--high-repeats",
            "10",
            "--high-repeat-datasets",
            "cesm_atm,miranda,noaa_gsod_csv",
            "--adaptive-max-samples",
            "2",
            "--adaptive-stability-cv",
            "0.20",
            "--probe-candidates",
            "fast",
        ]
        run_command(command, name="retry_main_failures", timeout=24 * 3600, check=False)
    retry_status_path = RETRY_RUN / "run_status.json"
    if not retry_status_path.exists():
        raise RuntimeError("retry run did not produce run_status.json")
    retry_status = json.loads(retry_status_path.read_text(encoding="utf-8"))
    if retry_status.get("status") != "completed" or int(retry_status.get("failed_rows", 1)):
        raise RuntimeError(f"retry run did not fully complete: {retry_status}")
    return RETRY_RUN


def merge_canonical(retry_dir: Path) -> Path:
    update_status("merge_main_csv", "running")
    base_rows = read_csv(SOURCE_RUN / "method_metrics.csv")
    retry_rows = read_csv(retry_dir / "method_metrics.csv")
    merged: dict[tuple[str, int, str], dict[str, str]] = {
        key(row): row for row in base_rows if row.get("status") == "completed" and row.get("roundtrip_ok") == "True"
    }
    replaced: list[tuple[str, int, str]] = []
    for row in retry_rows:
        if row.get("status") == "completed" and row.get("roundtrip_ok") == "True":
            task = key(row)
            if task not in merged or merged[task].get("run_id") != row.get("run_id"):
                replaced.append(task)
            merged[task] = row
    rows = [merged[item] for item in sorted(merged)]
    if len(rows) != 306 or len({key(row) for row in rows}) != 306:
        raise RuntimeError(f"canonical merge expected 306 unique rows, got {len(rows)}")
    if any(row.get("status") != "completed" or row.get("roundtrip_ok") != "True" for row in rows):
        raise RuntimeError("canonical merge contains failed or non-roundtripping rows")
    counts = Counter(row["dataset"] for row in rows)
    expected = {
        dataset: (10 if dataset in {"cesm_atm", "miranda", "noaa_gsod_csv"} else 3) * 6
        for dataset in ("cesm_atm", "exafel", "hacc", "hurricane_isabel", "miranda", "ncbi_grch38_fasta", "noaa_gsod_csv", "nyx", "rcsb_mmcif", "scale_letkf")
    }
    if dict(counts) != expected:
        raise RuntimeError(f"unexpected per-dataset counts: {dict(counts)}")

    FINAL_RUN.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys([name for row in base_rows + retry_rows for name in row]))
    normalized = []
    for row in rows:
        copied = dict(row)
        copied["run_id"] = CANONICAL_RUN_ID
        normalized.append(copied)
    write_csv(FINAL_RUN / "method_metrics.csv", normalized, fields)
    for name in ("runner_config.json", "dcc_protocol.json"):
        source = SOURCE_RUN / name
        if source.exists():
            shutil.copy2(source, FINAL_RUN / name)
    old_hashes = {}
    for name in ("Compressor/cpss_adaptive.py.pre_repair_20260928", "Compressor/deploy_registry.py.pre_repair_20260928"):
        path = PROJECT / name
        old_hashes[name] = sha256(path) if path.exists() else None
    new_hashes = {
        "Compressor/cpss_adaptive.py": sha256(PROJECT / "Compressor/cpss_adaptive.py"),
        "Compressor/deploy_registry.py": sha256(PROJECT / "Compressor/deploy_registry.py"),
    }
    write_json(FINAL_RUN / "code_hashes.json", {"pre_repair": old_hashes, "repaired": new_hashes})
    write_json(FINAL_RUN / "merge_report.json", {
        "status": "validated",
        "source_run": str(SOURCE_RUN),
        "retry_run": str(retry_dir),
        "canonical_csv": str(FINAL_RUN / "method_metrics.csv"),
        "row_count": len(normalized),
        "unique_key_count": len({key(row) for row in normalized}),
        "replaced_keys": [list(item) for item in sorted(set(replaced))],
        "failed_rows_removed": sum(row.get("status") != "completed" for row in base_rows),
        "run_id": CANONICAL_RUN_ID,
        "per_dataset_counts": dict(counts),
    })
    update_status("merge_main_csv", "completed", canonical_csv=str(FINAL_RUN / "method_metrics.csv"), row_count=len(normalized))
    return FINAL_RUN


def run_bandwidth_model(final_dir: Path) -> Path:
    update_status("bandwidth_sensitivity", "running")
    rows = read_csv(final_dir / "method_metrics.csv")
    output = WORK_ROOT / "bandwidth_sensitivity.csv"
    modeled: list[dict[str, Any]] = []
    for row in rows:
        try:
            ready = float(row["T_ready_s"])
            transfer = float(row["gridflux_transfer_time_s"])
            transferred = int(float(row["transferred_bytes"]))
        except (ValueError, KeyError):
            continue
        local = max(0.0, ready - transfer)
        for bandwidth in (10, 100, 1000):
            model_ready = local + transferred / ((bandwidth * 1_000_000) / 8.0)
            modeled.append({
                "dataset": row["dataset"],
                "repeat": row["repeat"],
                "method": row["method"],
                "bandwidth_mbps": bandwidth,
                "T_model_s": model_ready,
                "transferred_bytes": transferred,
                "local_component_s": local,
                "selected_action": row.get("selected_action", ""),
            })
    write_csv(output, modeled)
    grouped: dict[tuple[int, str, str], list[float]] = defaultdict(list)
    for row in modeled:
        grouped[(int(row["bandwidth_mbps"]), row["dataset"], row["method"])].append(float(row["T_model_s"]))
    summary: list[dict[str, Any]] = []
    for bandwidth in (10, 100, 1000):
        raw_by_dataset = {
            dataset: statistics.median(values)
            for (b, dataset, method), values in grouped.items()
            if b == bandwidth and method == "raw"
        }
        for method in ("raw", "gzip-6", "lz4", "zstd-3", "xz-6", "cpss-adaptive"):
            ratios = []
            raw_total = 0.0
            method_total = 0.0
            for dataset, raw_time in raw_by_dataset.items():
                values = grouped.get((bandwidth, dataset, method), [])
                if values and raw_time > 0:
                    method_time = statistics.median(values)
                    ratios.append(raw_time / method_time)
                    raw_total += raw_time
                    method_total += method_time
            summary.append({
                "bandwidth_mbps": bandwidth,
                "method": method,
                "datasets": len(ratios),
                "total_time_speedup": raw_total / method_total if method_total else "",
                "geomean_speedup": math.exp(sum(math.log(value) for value in ratios) / len(ratios)) if ratios else "",
            })
    write_csv(WORK_ROOT / "bandwidth_sensitivity_summary.csv", summary)
    write_json(WORK_ROOT / "bandwidth_sensitivity.json", {"bandwidths_mbps": [10, 100, 1000], "rows": len(modeled), "summary": summary})
    update_status("bandwidth_sensitivity", "completed", output=str(output))
    return output


def run_ablation() -> Path:
    ablation_dir = WORK_ROOT / "ablation_run"
    update_status("ablation", "running", result_dir=str(ablation_dir))
    status_path = ablation_dir / "run_status.json"
    if not status_path.exists() or json.loads(status_path.read_text(encoding="utf-8")).get("status") != "completed":
        command = [
            str(PYTHON), "Script/run_dcc_final_gridflux.py",
            "--dataset", f"cesm_atm={DATA_ROOT / 'cesm_atm/raw'}",
            "--dataset", f"miranda={DATA_ROOT / 'miranda/raw'}",
            "--dataset", f"noaa_gsod_csv={DATA_ROOT / 'noaa_gsod_csv/raw'}",
            "--methods", "raw,zstd-3,cpss-adaptive",
            "--repeats", str(REPEATS),
            "--high-repeats", str(REPEATS),
            "--high-repeat-datasets", "",
            "--run-id", ablation_dir.name,
            "--result-dir", str(ablation_dir),
            "--adaptive-max-samples", "2",
            "--adaptive-stability-cv", "0.20",
            "--probe-candidates", "fast",
        ]
        run_command(command, name="ablation", timeout=24 * 3600, check=False)
    if not status_path.exists():
        raise RuntimeError("ablation did not produce run_status.json")
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if status.get("status") != "completed" or int(status.get("failed_rows", 1)):
        raise RuntimeError(f"ablation failed: {status}")
    rows = read_csv(ablation_dir / "method_metrics.csv")
    oracle_by_dataset: dict[str, str] = {}
    for dataset in DATASETS:
        oracle_path = ablation_dir / "datasets" / dataset / "full_data_global_oracle.json"
        if oracle_path.exists():
            oracle_by_dataset[dataset] = str(json.loads(oracle_path.read_text(encoding="utf-8")).get("selected_pipeline", ""))
    summary: list[dict[str, Any]] = []
    by_task = {key(row): row for row in rows}
    for dataset in DATASETS:
        for repeat in range(1, REPEATS + 1):
            raw = by_task[(dataset, repeat, "raw")]
            zstd = by_task[(dataset, repeat, "zstd-3")]
            adaptive = by_task[(dataset, repeat, "cpss-adaptive")]
            oracle_pipeline = oracle_by_dataset.get(dataset, "")
            oracle_row = raw if oracle_pipeline == "raw" else zstd if oracle_pipeline == "zstd" else None
            for label, source in (
                ("CPSS-adaptive", adaptive),
                ("CPSS-static-zstd", zstd),
                ("forced-compression-zstd", zstd),
                ("CPSS-oracle", oracle_row),
            ):
                if source is None:
                    continue
                summary.append({
                    "dataset": dataset,
                    "repeat": repeat,
                    "ablation": label,
                    "source_method": source.get("method", ""),
                    "T_ready_s": source.get("T_ready_s", ""),
                    "selected_action": source.get("selected_action", ""),
                    "selected_pipeline": source.get("selected_pipeline", ""),
                    "oracle_pipeline": oracle_pipeline,
                })
    write_csv(WORK_ROOT / "ablation_summary.csv", summary)
    write_json(WORK_ROOT / "ablation_manifest.json", {
        "result_dir": str(ablation_dir),
        "datasets": list(DATASETS),
        "methods": list(METHODS),
        "repeats": REPEATS,
        "oracle_source": "full_data_global_oracle.json generated by the same run",
        "CPSS-static-zstd": "fixed Zstd-3 through the same CPSS/GridFlux path",
        "forced-compression-zstd": "same fixed Zstd path reported as the no-Raw-fallback stress case",
    })
    update_status("ablation", "completed", result_dir=str(ablation_dir))
    return ablation_dir


def source_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def remote_shell(command: str, *, name: str, timeout: int = 3600, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new", REMOTE_ALIAS, command], name=name, timeout=timeout, check=check)


def run_scp_validation(ablation_dir: Path) -> Path:
    update_status("scp_cross_validation", "running")
    cross_dir = WORK_ROOT / "gridflux_scp_cross_validation"
    cross_dir.mkdir(parents=True, exist_ok=True)
    result_path = cross_dir / "scp_metrics.csv"
    existing = {(row.get("dataset"), int(row.get("repeat", 0)), row.get("method")): row for row in read_csv(result_path)} if result_path.exists() else {}
    ablation_rows = {key(row): row for row in read_csv(ablation_dir / "method_metrics.csv")}
    fieldnames = ["dataset", "repeat", "method", "payload_mode", "source_bytes", "archive_bytes", "scp_time_s", "extract_time_s", "total_time_s", "remote_bytes", "roundtrip_ok", "status", "error"]
    work = Path(tempfile.mkdtemp(prefix="dcc_scp_", dir=str(WORK_ROOT)))
    try:
        for dataset in DATASETS:
            source = DATA_ROOT / dataset / "raw"
            raw_size = source_bytes(source)
            adaptive_rows = [ablation_rows[(dataset, repeat, "cpss-adaptive")] for repeat in range(1, REPEATS + 1)]
            modes: list[tuple[str, str]] = [("raw", "raw"), ("zstd-3", "zstd")]
            modes.extend(("cpss-adaptive", "raw" if row.get("selected_action") == "raw" else "zstd") for row in adaptive_rows)
            for method, payload_mode in dict.fromkeys(modes):
                archive_name = f"{dataset}_{method.replace('-', '_')}.tar" + (".zst" if payload_mode == "zstd" else "")
                archive = work / archive_name
                if payload_mode == "raw":
                    run_command(["tar", "-cf", str(archive), "-C", str(source), "."], name=f"scp_pack_{dataset}_{method}", timeout=6 * 3600)
                else:
                    run_command(["tar", "-I", "zstd -3 -T1", "-cf", str(archive), "-C", str(source), "."], name=f"scp_pack_{dataset}_{method}", timeout=6 * 3600)
                for repeat in range(1, REPEATS + 1):
                    task = (dataset, repeat, method)
                    if task in existing and existing[task].get("status") == "completed":
                        continue
                    remote_base = f"/tmp/dcc_scp_cross_validation_20260929/{dataset}_{method.replace('-', '_')}_rep{repeat}"
                    remote_archive = f"{remote_base}.tar" + (".zst" if payload_mode == "zstd" else "")
                    remote_out = f"{remote_base}.out"
                    remote_shell(f"rm -rf -- {shlex.quote(remote_out)} {shlex.quote(remote_archive)} && mkdir -p -- {shlex.quote(remote_out)}", name=f"scp_prepare_{dataset}_{method}_{repeat}", check=True)
                    started = time.perf_counter()
                    scp_result = run_command(["scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", str(archive), f"{REMOTE_ALIAS}:{remote_archive}"], name=f"scp_transfer_{dataset}_{method}_{repeat}", timeout=6 * 3600, check=False)
                    scp_elapsed = time.perf_counter() - started
                    extract_started = time.perf_counter()
                    extractor = "tar --zstd" if payload_mode == "zstd" else "tar"
                    extract = remote_shell(f"{extractor} -xf {shlex.quote(remote_archive)} -C {shlex.quote(remote_out)} && find {shlex.quote(remote_out)} -type f -printf '%s\\n' | awk '{{s+=$1}} END{{print s+0}}'", name=f"scp_extract_{dataset}_{method}_{repeat}", timeout=6 * 3600, check=False) if scp_result.returncode == 0 else None
                    extract_elapsed = time.perf_counter() - extract_started
                    remote_total = int((extract.stdout or "0").strip().splitlines()[-1]) if extract and extract.stdout.strip().splitlines() else -1
                    ok = bool(scp_result.returncode == 0 and extract and extract.returncode == 0 and remote_total == raw_size)
                    row = {
                        "dataset": dataset, "repeat": repeat, "method": method, "payload_mode": payload_mode,
                        "source_bytes": raw_size, "archive_bytes": archive.stat().st_size, "scp_time_s": scp_elapsed,
                        "extract_time_s": extract_elapsed, "total_time_s": scp_elapsed + extract_elapsed,
                        "remote_bytes": remote_total, "roundtrip_ok": ok, "status": "completed" if ok else "failed",
                        "error": "" if ok else (scp_result.stderr or (extract.stderr if extract else "scp failed"))[-1000:],
                    }
                    existing[task] = row
                    write_csv(result_path, existing.values(), fieldnames)
                    remote_shell(f"rm -rf -- {shlex.quote(remote_out)} {shlex.quote(remote_archive)}", name=f"scp_cleanup_{dataset}_{method}_{repeat}", check=False)
                archive.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rows = read_csv(result_path)
    failures = [row for row in rows if row.get("status") != "completed"]
    write_json(cross_dir / "cross_validation_report.json", {
        "status": "validated" if len(rows) == 27 and not failures else "incomplete",
        "gridflux_source": str(ablation_dir / "method_metrics.csv"),
        "scp_rows": len(rows),
        "scp_failures": len(failures),
        "methods": ["raw", "zstd-3", "cpss-adaptive"],
        "datasets": list(DATASETS),
        "repeats": REPEATS,
    })
    update_status("scp_cross_validation", "completed" if not failures else "completed_with_failures", rows=len(rows), failures=len(failures))
    return result_path


def main() -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        main_status = wait_for_main_run()
        retry_dir = retry_failed_main_rows()
        final_dir = merge_canonical(retry_dir)
        run_bandwidth_model(final_dir)
        ablation_dir = run_ablation()
        # The representative ablation uses exactly the frozen GridFlux matrix
        # needed by the GridFlux side of the 3x3x3 transport cross-check.
        cross_path = run_scp_validation(ablation_dir)
        update_status("done", "completed", main_status=main_status, cross_validation=str(cross_path))
        return 0
    except Exception as exc:
        update_status("failed", "failed", error=f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    raise SystemExit(main())

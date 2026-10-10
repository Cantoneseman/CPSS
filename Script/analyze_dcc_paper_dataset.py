#!/usr/bin/env python3
"""Audit and summarize the CPSS DCC experiment package without pooling runs.

The raw experiment files remain untouched.  Outputs are written to the
package's ``analysis/`` directory and retain experiment-group boundaries.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any


METHOD_ORDER = ["raw", "gzip-6", "lz4", "zstd-3", "xz-6", "cpss-adaptive"]
BASELINES = ["raw", "zstd-3"]
MIB = 1024 * 1024


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str, default: float = math.nan) -> float:
    try:
        value = row.get(key, "")
        return default if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return default


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def geomean(values: list[float]) -> float:
    positive = [value for value in values if math.isfinite(value) and value > 0]
    return math.exp(sum(math.log(value) for value in positive) / len(positive)) if positive else math.nan


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def group_by(rows: list[dict[str, str]], *keys: str) -> dict[tuple[str, ...], list[dict[str, str]]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row.get(key, "") for key in keys)].append(row)
    return groups


def summarize_methods(rows: list[dict[str, str]], group_name: str, group_field: str = "") -> list[dict[str, Any]]:
    scopes: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        scope = row.get(group_field, "all") if group_field else "all"
        scopes[str(scope)].append(row)

    output: list[dict[str, Any]] = []
    for scope, scope_rows in sorted(scopes.items()):
        by_dm = group_by(scope_rows, "dataset", "method")
        medians: dict[tuple[str, str], float] = {}
        for key, method_rows in by_dm.items():
            values = [number(row, "T_ready_s") for row in method_rows if math.isfinite(number(row, "T_ready_s"))]
            if values:
                medians[key] = median(values)
        datasets = sorted({dataset for dataset, _method in medians})
        raw_values = [medians[(dataset, "raw")] for dataset in datasets if (dataset, "raw") in medians]
        raw_sum = sum(raw_values)
        for method in METHOD_ORDER:
            method_values = [medians[(dataset, method)] for dataset in datasets if (dataset, method) in medians]
            if not method_values:
                continue
            paired = [
                (medians[(dataset, "raw")], medians[(dataset, method)])
                for dataset in datasets
                if (dataset, "raw") in medians and (dataset, method) in medians
            ]
            ratios = [raw / candidate for raw, candidate in paired if candidate > 0]
            wins = sum(candidate < raw for raw, candidate in paired)
            action_counts = Counter(row.get("selected_action", "") for row in scope_rows if row.get("method") == method)
            pipeline_counts = Counter(row.get("selected_pipeline", "") for row in scope_rows if row.get("method") == method)
            source_bytes = sorted({int(number(row, "raw_bytes")) for row in scope_rows if row.get("method") == method and math.isfinite(number(row, "raw_bytes"))})
            output.append({
                "experiment_group": group_name,
                "scope": scope,
                "method": method,
                "datasets": len(method_values),
                "rows": sum(row.get("method") == method for row in scope_rows),
                "median_sum_T_ready_s": sum(method_values),
                "speedup_vs_raw": raw_sum / sum(method_values) if method_values and sum(method_values) else math.nan,
                "geomean_speedup_vs_raw": geomean(ratios),
                "wins_vs_raw": wins,
                "losses_vs_raw": len(paired) - wins,
                "selected_actions": json.dumps(dict(action_counts), ensure_ascii=False, sort_keys=True),
                "selected_pipelines": json.dumps(dict(pipeline_counts), ensure_ascii=False, sort_keys=True),
                "input_bytes_min": min(source_bytes) if source_bytes else "",
                "input_bytes_max": max(source_bytes) if source_bytes else "",
            })
    return output


def summarize_dataset_methods(rows: list[dict[str, str]], group_name: str, scope_field: str = "") -> list[dict[str, Any]]:
    scopes: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        scope = str(row.get(scope_field, "all")) if scope_field else "all"
        scopes[scope].append(row)
    output = []
    for scope, scope_rows in sorted(scopes.items()):
        for (dataset, method), values in sorted(group_by(scope_rows, "dataset", "method").items()):
            times = [number(row, "T_ready_s") for row in values if math.isfinite(number(row, "T_ready_s"))]
            if not times:
                continue
            raw_rows = [row for row in scope_rows if row.get("dataset") == dataset and row.get("method") == "raw"]
            raw_time = median([number(row, "T_ready_s") for row in raw_rows]) if raw_rows else math.nan
            cp_rows = [row for row in values if method == "cpss-adaptive"]
            samples = [number(row, "sample_count") for row in cp_rows if math.isfinite(number(row, "sample_count"))]
            probes = [number(row, "probe_time_s") for row in cp_rows if math.isfinite(number(row, "probe_time_s"))]
            output.append({
                "experiment_group": group_name,
                "scope": scope,
                "dataset": dataset,
                "method": method,
                "repeats": len(times),
                "median_T_ready_s": median(times),
                "min_T_ready_s": min(times),
                "max_T_ready_s": max(times),
                "speedup_vs_raw": raw_time / median(times) if raw_time and median(times) else math.nan,
                "actions": json.dumps(dict(Counter(row.get("selected_action", "") for row in cp_rows)), sort_keys=True),
                "selected_pipelines": json.dumps(dict(Counter(row.get("selected_pipeline", "") for row in cp_rows)), sort_keys=True),
                "median_actual_sample_count": median(samples) if samples else "",
                "median_probe_s": median(probes) if probes else "",
                "median_regret_field": median([number(row, "system_regret_vs_oracle") for row in cp_rows]) if cp_rows else "",
            })
    return output


def summarize_sampling(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    by_budget_dataset_method = group_by(rows, "sampling_budget", "dataset", "method")
    for (budget, dataset, method), values in sorted(by_budget_dataset_method.items(), key=lambda item: (int(item[0][0] or 0), item[0][1], item[0][2])):
        cp = [row for row in values if method == "cpss-adaptive"]
        probes = [number(row, "probe_time_s") for row in cp if math.isfinite(number(row, "probe_time_s"))]
        samples = [number(row, "sample_count") for row in cp if math.isfinite(number(row, "sample_count"))]
        output.append({
            "sampling_budget": int(budget),
            "dataset": dataset,
            "method": method,
            "rows": len(values),
            "median_T_ready_s": median([number(row, "T_ready_s") for row in values]),
            "selected_actions": json.dumps(dict(Counter(row.get("selected_action", "") for row in cp)), sort_keys=True),
            "selected_pipelines": json.dumps(dict(Counter(row.get("selected_pipeline", "") for row in cp)), sort_keys=True),
            "median_actual_sample_count": median(samples) if samples else "",
            "median_sampled_MiB": median(samples) * 4 if samples else "",
            "median_probe_s": median(probes) if probes else "",
            "median_probe_share_pct": 100 * median(probes) / median([number(row, "T_ready_s") for row in cp]) if probes and cp else "",
            "median_legacy_regret_field_pct": 100 * median([number(row, "system_regret_vs_oracle") for row in cp]) if cp else "",
        })

    # A measured two-action reference keeps the exact same dataset, repetition,
    # link, and timing boundary. It is intentionally not called a global oracle.
    by_budget_pair_method = group_by(rows, "sampling_budget", "dataset", "repeat", "method")
    pair_index: dict[tuple[str, str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    for (budget, dataset, repeat, method), values in by_budget_pair_method.items():
        if values:
            pair_index[(budget, dataset, repeat)][method] = values[0]
    for budget, dataset, repeat in sorted(pair_index, key=lambda item: (int(item[0] or 0), item[1], int(item[2] or 0))):
        group = pair_index[(budget, dataset, repeat)]
        cp = group.get("cpss-adaptive")
        raw = group.get("raw")
        zstd = group.get("zstd-3")
        if not cp or not raw or not zstd:
            continue
        raw_t, zstd_t, cp_t = (number(item, "T_ready_s") for item in (raw, zstd, cp))
        best_action = "raw" if raw_t <= zstd_t else "compress"
        best_time = min(raw_t, zstd_t)
        cp.setdefault("_audit", {})
        cp["_audit"] = {
            "sampling_budget": int(budget),
            "dataset": dataset,
            "repeat": int(repeat),
            "reference_best_action": best_action,
            "reference_best_T_ready_s": best_time,
            "cpss_action": cp.get("selected_action", ""),
            "action_agreement": cp.get("selected_action", "") == best_action,
            "cpss_T_ready_s": cp_t,
            "regret_vs_paired_measured_raw_zstd_pct": 100 * (cp_t / best_time - 1) if best_time > 0 else math.nan,
            "probe_time_s": number(cp, "probe_time_s"),
            "sample_count": number(cp, "sample_count"),
        }
    references = [
        row["_audit"] for row in rows
        if isinstance(row.get("_audit"), dict)
    ]
    for budget in sorted({int(row["sampling_budget"]) for row in references}):
        budget_rows = [row for row in references if row["sampling_budget"] == budget]
        output.append({
            "sampling_budget": budget,
            "dataset": "ALL",
            "method": "paired_best_of_raw_zstd_reference",
            "rows": len(budget_rows),
            "median_T_ready_s": median([row["cpss_T_ready_s"] for row in budget_rows]),
            "action_agreement_pct": 100 * sum(row["action_agreement"] for row in budget_rows) / len(budget_rows),
            "median_regret_vs_paired_measured_raw_zstd_pct": median([row["regret_vs_paired_measured_raw_zstd_pct"] for row in budget_rows]),
            "median_actual_sample_count": median([row["sample_count"] for row in budget_rows]),
            "median_sampled_MiB": 4 * median([row["sample_count"] for row in budget_rows]),
            "median_probe_s": median([row["probe_time_s"] for row in budget_rows]),
        })
    return output


def audit_package(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches = []
    for item in manifest.get("files", []):
        path = root / item["path"]
        if not path.is_file():
            mismatches.append({"path": item["path"], "issue": "missing"})
            continue
        if path.stat().st_size != item.get("bytes") or digest(path) != item.get("sha256"):
            mismatches.append({"path": item["path"], "issue": "size_or_hash_mismatch"})

    server = root / "raw" / "server_20261004"
    runs = [
        server / "dcc_codec_pool_20261002",
        server / "dcc_sampling_budget_20261003_restart2" / "budget_1",
        server / "dcc_sampling_budget_20261003_restart2" / "budget_2",
        server / "dcc_sampling_budget_20261003_restart2" / "budget_5",
        server / "dcc_raw_fallback_ablation_20261003_resume3",
        server / "dcc_bandwidth_sensitivity_20261003" / "10p0mbps",
        server / "dcc_bandwidth_sensitivity_20261003" / "100p0mbps",
    ]
    statuses = []
    for run_dir in runs:
        status_path = run_dir / "run_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {"status": "missing"}
        metrics_path = run_dir / "method_metrics.csv"
        rows = read_csv(metrics_path) if metrics_path.is_file() else []
        keys = [(row.get("run_id", ""), row.get("dataset", ""), row.get("repeat", ""), row.get("method", "")) for row in rows]
        bad_integrity = [
            row for row in rows
            if row.get("status") != "completed" or row.get("roundtrip_ok", "").lower() != "true"
        ]
        statuses.append({
            "run_dir": run_dir.relative_to(root).as_posix(),
            "status": status.get("status"),
            "declared_rows": status.get("row_count"),
            "failed_rows": status.get("failed_rows"),
            "csv_rows": len(rows),
            "unique_keys": len(set(keys)),
            "integrity_failures": len(bad_integrity),
        })
    return {
        "package_id": manifest.get("dataset_id"),
        "manifest_entries": len(manifest.get("files", [])),
        "manifest_mismatches": mismatches,
        "server_runs": statuses,
        "new_server_raw_rows": sum(item["csv_rows"] for item in statuses),
        "new_server_unique_rows": sum(item["unique_keys"] for item in statuses),
        "new_server_integrity_failures": sum(item["integrity_failures"] for item in statuses),
    }


def fmt(value: Any, digits: int = 2) -> str:
    try:
        number_value = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number_value:.{digits}f}" if math.isfinite(number_value) else "n/a"


def write_report(path: Path, audit: dict[str, Any], method_rows: list[dict[str, Any]], sampling_rows: list[dict[str, Any]], bandwidth_rows: list[dict[str, Any]], fallback_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# DCC Experiment Evidence Audit (2026-10-04)",
        "",
        "## Integrity",
        f"- Package: `{audit['package_id']}`; manifest entries checked: {audit['manifest_entries']}; file mismatches: {len(audit['manifest_mismatches'])}.",
        f"- New Shenzhen result rows: {audit['new_server_raw_rows']}; unique keys: {audit['new_server_unique_rows']}; failed round-trip/status rows: {audit['new_server_integrity_failures']}.",
        "- Experiment groups are summarized separately. The configured bandwidth value is a CPSS cost-model input; the GridFlux transfer path is not rate-shaped by this command-line argument.",
        "",
        "## Main and Codec-Pool Results",
        "| Group / scope | Method | Median-sum T_ready (s) | Speedup vs Raw | Wins--losses | Actions |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in method_rows:
        if row["method"] not in {"raw", "zstd-3", "lz4", "gzip-6", "xz-6", "cpss-adaptive"}:
            continue
        lines.append(
            f"| {row['experiment_group']} / {row['scope']} | {row['method']} | {fmt(row['median_sum_T_ready_s'])} | {fmt(row['speedup_vs_raw'], 3)} | {row['wins_vs_raw']}--{row['losses_vs_raw']} | {row['selected_actions']} |"
        )
    lines.extend([
        "",
        "## Sampling Budget",
        "| Budget | Dataset | Method | Median T_ready (s) | Actions | Median samples | Probe (s) |",
        "|---:|---|---|---:|---|---:|---:|",
    ])
    for row in sampling_rows:
        if row["dataset"] == "ALL":
            lines.append(
                f"| {row['sampling_budget']} | ALL | paired measured Raw/Zstd reference | {fmt(row.get('median_T_ready_s'))} | agreement {fmt(row.get('action_agreement_pct'))}% | {fmt(row.get('median_actual_sample_count'))} | {fmt(row.get('median_probe_s'))} | paired regret {fmt(row.get('median_regret_vs_paired_measured_raw_zstd_pct'))} |"
            )
        elif row["method"] == "cpss-adaptive":
            lines.append(
                f"| {row['sampling_budget']} | {row['dataset']} | CPSS | {fmt(row['median_T_ready_s'])} | {row['selected_actions']} | {fmt(row.get('median_actual_sample_count'))} | {fmt(row.get('median_probe_s'))} |"
            )
    lines.extend([
        "",
        "Paired reference summary (same dataset and repetition; best observed $T_{ready}$ between direct Raw and fixed Zstandard-3):",
        "| Budget | Valid pairs | Action agreement | Median CPSS regret vs measured two-action reference | Median samples | Median probe (s) |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for row in sampling_rows:
        if row["dataset"] == "ALL":
            lines.append(
                f"| {row['sampling_budget']} | {row['rows']} | {fmt(row.get('action_agreement_pct'))}% | {fmt(row.get('median_regret_vs_paired_measured_raw_zstd_pct'))}% | {fmt(row.get('median_actual_sample_count'))} | {fmt(row.get('median_probe_s'))} |"
            )
    lines.extend([
        "",
        "## Assumed-Bandwidth Sensitivity",
        "| Assumed rate (Mbps) | Method | Median-sum T_ready (s) | Speedup vs Raw | Actions |",
        "|---:|---|---:|---:|---|",
    ])
    for row in bandwidth_rows:
        lines.append(
            f"| {row['scope']} | {row['method']} | {fmt(row['median_sum_T_ready_s'])} | {fmt(row['speedup_vs_raw'], 3)} | {row['selected_actions']} |"
        )
    lines.extend([
        "",
        "## Raw Fallback Workloads",
        "| Dataset | Method | Median T_ready (s) | Actions | Speedup vs Raw |",
        "|---|---|---:|---|---:|",
    ])
    for row in fallback_rows:
        lines.append(
            f"| {row['scope']} | {row['method']} | {fmt(row['median_sum_T_ready_s'])} | {row['selected_actions']} | {fmt(row['speedup_vs_raw'], 3)} |"
        )
    lines.extend([
        "",
        "## Interpretation Boundary",
        "- The new codec-pool run gives an end-to-end comparison against fixed codecs under a CPSS candidate set of Zstandard, LZ4, zlib, and XZ. It does not run a trained SFS+1NN selector through GridFlux, so it is not a direct end-to-end MLcomp comparison.",
        "- The sampling CSV's `system_regret_vs_oracle` is retained only as a diagnostic because its full-data reference is estimated from codec time plus configured-rate wire time and does not share all measured `T_ready` stages. The paired measured Raw/Zstd reference is reported separately and is not a global oracle.",
        "- The bandwidth runs vary the rate assumption used by CPSS, not the physical GridFlux throughput. Report them as decision-model sensitivity unless separate traffic shaping is documented.",
        "- The Raw-fallback run compares CPSS with direct Raw and fixed Zstandard. It is not a separate CPSS execution with the bypass branch forcibly disabled.",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.dataset_root.resolve()
    derived = root / "derived"
    out = root / "analysis"

    audit = audit_package(root)
    codec = read_csv(derived / "codec_pool_method_metrics.csv")
    main_rows = read_csv(derived / "legacy_main_method_metrics.csv")
    sampling = read_csv(derived / "sampling_budget_method_metrics.csv")
    bandwidth = read_csv(derived / "bandwidth_sensitivity_method_metrics.csv")
    fallback = read_csv(derived / "raw_fallback_method_metrics.csv")

    method_summary = summarize_methods(main_rows, "legacy_main_20260930")
    method_summary += summarize_methods(codec, "codec_pool_20261002")
    sampling_summary = summarize_sampling(sampling)
    bandwidth_summary = summarize_methods(bandwidth, "bandwidth_sensitivity_20261003", "simulated_bandwidth_mbps")
    fallback_summary = summarize_methods(fallback, "raw_fallback_20261003")

    write_csv(out / "method_aggregate_summary.csv", method_summary)
    dataset_summary = summarize_dataset_methods(main_rows, "legacy_main_20260930")
    dataset_summary += summarize_dataset_methods(codec, "codec_pool_20261002")
    dataset_summary += summarize_dataset_methods(fallback, "raw_fallback_20261003")
    dataset_summary += summarize_dataset_methods(bandwidth, "bandwidth_sensitivity_20261003", "simulated_bandwidth_mbps")
    write_csv(out / "dataset_method_summary.csv", dataset_summary)
    write_csv(out / "sampling_budget_summary.csv", sampling_summary)
    write_csv(out / "assumed_bandwidth_summary.csv", bandwidth_summary)
    write_csv(out / "raw_fallback_summary.csv", fallback_summary)
    (out / "data_quality_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(out / "analysis_report.md", audit, method_summary, sampling_summary, bandwidth_summary, fallback_summary)
    print(json.dumps({
        "analysis_dir": str(out),
        "manifest_mismatches": len(audit["manifest_mismatches"]),
        "new_server_rows": audit["new_server_raw_rows"],
        "new_server_integrity_failures": audit["new_server_integrity_failures"],
        "report": str(out / "analysis_report.md"),
    }, ensure_ascii=False, indent=2))
    return 0 if not audit["manifest_mismatches"] and audit["new_server_integrity_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

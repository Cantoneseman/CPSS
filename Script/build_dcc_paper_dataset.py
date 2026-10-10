#!/usr/bin/env python3
"""Build analysis-ready indexes for the DCC paper experiment archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_metric(path: Path, group: str, **tags: object) -> tuple[list[str], list[dict[str, object]]]:
    fields, source_rows = read_csv(path)
    rows: list[dict[str, object]] = []
    for source_row in source_rows:
        row: dict[str, object] = dict(source_row)
        row["experiment_group"] = group
        row["source_file"] = path.as_posix()
        row.update(tags)
        rows.append(row)
    return fields, rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    server = root / "raw" / "server_20261004"
    legacy = root / "raw" / "legacy_20260930"
    derived = root / "derived"
    derived.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[dict[str, object]]] = {}
    metric_columns: list[str] = []

    def add_group(name: str, path: Path, **tags: object) -> None:
        nonlocal metric_columns
        _, rows = load_metric(path, name, **tags)
        groups.setdefault(name, []).extend(rows)
        metric_columns = list(dict.fromkeys(metric_columns + list(rows[0].keys()))) if rows else metric_columns

    add_group(
        "legacy_main_20260930",
        legacy / "analysis" / "main_method_metrics.csv",
        data_role="historical_main_baseline",
    )
    add_group(
        "legacy_ablation_20260929",
        legacy / "analysis" / "ablation_method_metrics.csv",
        data_role="historical_ablation",
    )
    add_group(
        "legacy_10mbps_preflight_20261001",
        legacy
        / "ExperimentRecords"
        / "dcc_10mbps_subset_preflight128"
        / "run"
        / "method_metrics.csv",
        data_role="historical_preflight_not_primary",
    )
    add_group(
        "legacy_mlcomp_like_e2e_20261002",
        legacy / "ExperimentRecords" / "dcc_mlcomp_like_e2e_20261002" / "method_metrics.csv",
        data_role="historical_end_to_end_comparison",
    )
    add_group(
        "codec_pool_20261002",
        server / "dcc_codec_pool_20261002" / "method_metrics.csv",
        data_role="new_codec_comparison",
    )
    for budget_dir in sorted((server / "dcc_sampling_budget_20261003_restart2").glob("budget_*")):
        if not budget_dir.is_dir():
            continue
        budget = budget_dir.name.removeprefix("budget_")
        add_group(
            "sampling_budget_20261003",
            budget_dir / "method_metrics.csv",
            sampling_budget=budget,
            data_role="new_sampling_probe_cost",
        )
    add_group(
        "raw_fallback_20261003",
        server / "dcc_raw_fallback_ablation_20261003_resume3" / "method_metrics.csv",
        data_role="new_raw_fallback_ablation",
    )
    for bandwidth_dir in sorted((server / "dcc_bandwidth_sensitivity_20261003").glob("*mbps")):
        if not bandwidth_dir.is_dir():
            continue
        bandwidth = bandwidth_dir.name.removesuffix("mbps").replace("p", ".")
        add_group(
            "bandwidth_sensitivity_20261003",
            bandwidth_dir / "method_metrics.csv",
            simulated_bandwidth_mbps=bandwidth,
            data_role="new_bandwidth_sensitivity",
        )
    add_group(
        "legacy_measured_bandwidth_20261001",
        legacy
        / "ExperimentRecords"
        / "dcc_bandwidth_sensitivity_measured_10_100_same128"
        / "method_metrics_10_100mbps.csv",
        data_role="historical_measured_bandwidth",
    )

    for group, rows in groups.items():
        out_name = {
            "legacy_main_20260930": "legacy_main_method_metrics.csv",
            "legacy_ablation_20260929": "legacy_ablation_method_metrics.csv",
            "legacy_10mbps_preflight_20261001": "legacy_10mbps_preflight_method_metrics.csv",
            "legacy_mlcomp_like_e2e_20261002": "legacy_mlcomp_like_e2e_method_metrics.csv",
            "codec_pool_20261002": "codec_pool_method_metrics.csv",
            "sampling_budget_20261003": "sampling_budget_method_metrics.csv",
            "raw_fallback_20261003": "raw_fallback_method_metrics.csv",
            "bandwidth_sensitivity_20261003": "bandwidth_sensitivity_method_metrics.csv",
            "legacy_measured_bandwidth_20261001": "legacy_measured_bandwidth_method_metrics.csv",
        }[group]
        write_csv(derived / out_name, metric_columns, rows)

    mlcomp_sources = [
        ("mlcomp_like_small_20261004", server / "dcc_mlcomp_like_20261004" / "mlcomp_like_small.csv"),
        (
            "legacy_mlcomp_like_small_20261001",
            legacy / "ExperimentRecords" / "dcc_mlcomp_like_small_20261001" / "mlcomp_like_small.csv",
        ),
    ]
    mlcomp_groups: dict[str, list[dict[str, object]]] = {}
    for name, path in mlcomp_sources:
        _, source_rows = read_csv(path)
        mlcomp_groups[name] = [dict(row, experiment_group=name, source_file=path.as_posix()) for row in source_rows]
    mlcomp_rows = [row for rows in mlcomp_groups.values() for row in rows]
    write_csv(
        derived / "mlcomp_like_small_summary.csv",
        list(mlcomp_rows[0].keys()) if mlcomp_rows else ["dataset", "predicted_pipeline", "oracle_pipeline"],
        mlcomp_rows,
    )

    scp_path = legacy / "analysis" / "scp_metrics.csv"
    scp_fields, scp_rows = read_csv(scp_path)
    scp_rows = [dict(row, experiment_group="legacy_scp_cross_validation_20260930", source_file=scp_path.as_posix()) for row in scp_rows]
    write_csv(derived / "legacy_scp_cross_validation.csv", scp_fields + ["experiment_group", "source_file"], scp_rows)

    # This index is for traceability only. Rows from different experiment groups
    # have different protocols and must not be pooled into one performance mean.
    all_rows = [row for rows in groups.values() for row in rows]
    write_csv(derived / "all_e2e_metrics_index.csv", metric_columns, all_rows)

    experiment_index: list[dict[str, object]] = []
    derived_names = {
        "legacy_main_20260930": "legacy_main_method_metrics.csv",
        "legacy_ablation_20260929": "legacy_ablation_method_metrics.csv",
        "legacy_10mbps_preflight_20261001": "legacy_10mbps_preflight_method_metrics.csv",
        "legacy_mlcomp_like_e2e_20261002": "legacy_mlcomp_like_e2e_method_metrics.csv",
        "codec_pool_20261002": "codec_pool_method_metrics.csv",
        "sampling_budget_20261003": "sampling_budget_method_metrics.csv",
        "raw_fallback_20261003": "raw_fallback_method_metrics.csv",
        "bandwidth_sensitivity_20261003": "bandwidth_sensitivity_method_metrics.csv",
        "legacy_measured_bandwidth_20261001": "legacy_measured_bandwidth_method_metrics.csv",
    }
    for group, rows in groups.items():
        datasets = sorted({str(row.get("dataset", "")) for row in rows if row.get("dataset")})
        methods = sorted({str(row.get("method", "")) for row in rows if row.get("method")})
        statuses: dict[str, int] = defaultdict(int)
        for row in rows:
            statuses[str(row.get("status", "not_applicable"))] += 1
        sizes = [int(row["raw_bytes"]) for row in rows if str(row.get("raw_bytes", "")).isdigit()]
        experiment_index.append(
            {
                "experiment_group": group,
                "rows": len(rows),
                "datasets": ";".join(datasets),
                "methods": ";".join(methods),
                "raw_size_min_bytes": min(sizes) if sizes else "",
                "raw_size_max_bytes": max(sizes) if sizes else "",
                "status_counts_json": json.dumps(statuses, sort_keys=True),
                "derived_file": derived_names[group],
            }
        )
    for group, rows in mlcomp_groups.items():
        experiment_index.append(
            {
                "experiment_group": group,
                "rows": len(rows),
                "datasets": ";".join(sorted({str(row.get("dataset", "")) for row in rows})),
                "methods": "MLcomp-like pipeline selector; not end-to-end GridFlux timing",
                "raw_size_min_bytes": "",
                "raw_size_max_bytes": "",
                "status_counts_json": "{}",
                "derived_file": "mlcomp_like_small_summary.csv",
            }
        )
    experiment_index.append(
        {
            "experiment_group": "legacy_scp_cross_validation_20260930",
            "rows": len(scp_rows),
            "datasets": ";".join(sorted({str(row.get("dataset", "")) for row in scp_rows})),
            "methods": ";".join(sorted({str(row.get("method", "")) for row in scp_rows})),
            "raw_size_min_bytes": "",
            "raw_size_max_bytes": "",
            "status_counts_json": json.dumps({"completed": sum(row.get("status") == "completed" for row in scp_rows)}),
            "derived_file": "legacy_scp_cross_validation.csv",
        }
    )
    write_csv(
        derived / "experiment_index.csv",
        ["experiment_group", "rows", "datasets", "methods", "raw_size_min_bytes", "raw_size_max_bytes", "status_counts_json", "derived_file"],
        experiment_index,
    )

    dataset_rows: dict[str, dict[str, object]] = {}
    for group, rows in groups.items():
        for row in rows:
            dataset = str(row.get("dataset", ""))
            raw_bytes = row.get("raw_bytes", "")
            if not dataset:
                continue
            entry = dataset_rows.setdefault(dataset, {"dataset": dataset, "observed_raw_bytes": set(), "experiment_groups": set(), "methods": set()})
            if str(raw_bytes).isdigit():
                entry["observed_raw_bytes"].add(int(str(raw_bytes)))
            entry["experiment_groups"].add(group)
            if row.get("method"):
                entry["methods"].add(str(row["method"]))
    inventory = []
    for dataset, entry in sorted(dataset_rows.items()):
        byte_values = sorted(entry["observed_raw_bytes"])
        inventory.append(
            {
                "dataset": dataset,
                "raw_bytes_values": ";".join(map(str, byte_values)),
                "size_mib_values": ";".join(f"{value / 1048576:.3f}" for value in byte_values),
                "experiment_groups": ";".join(sorted(entry["experiment_groups"])),
                "methods_observed": ";".join(sorted(entry["methods"])),
            }
        )
    write_csv(
        derived / "dataset_inventory.csv",
        ["dataset", "raw_bytes_values", "size_mib_values", "experiment_groups", "methods_observed"],
        inventory,
    )

    tracked_files = [
        path
        for path in root.rglob("*")
        if path.is_file() and ".transfer" not in path.parts and path.name not in {"manifest.json"}
    ]
    manifest = {
        "dataset_id": root.name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_roots": {
            "legacy_local": "science-compressor/ExperimentRecords/dcc_analysis_20260930",
            "legacy_audit_local": "science-compressor/ExperimentRecords/dcc_unified_audit_20261002",
            "new_server": "/root/projects/CPSS(DCC)/ExperimentRecords (Shenzhen)",
        },
        "server_run_directories": [
            "dcc_codec_pool_20261002",
            "dcc_sampling_budget_20261003_restart2",
            "dcc_raw_fallback_ablation_20261003_resume3",
            "dcc_bandwidth_sensitivity_20261003",
            "dcc_mlcomp_like_20261004",
        ],
        "server_transfer_scope": "CSV/JSON/MD and execution/configuration artifacts; large transfer payloads/dataset copies excluded",
        "important_analysis_rule": "Do not pool performance rows across experiment_group: dataset size, bandwidth, and protocol differ.",
        "file_count": len(tracked_files),
        "files": [
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in sorted(tracked_files)
        ],
    }
    write_json(root / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

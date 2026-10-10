"""Audit and normalize existing DCC experiment records without changing them.

The script deliberately produces a new report directory.  It never edits an
existing CSV, manifest, dataset, or remote experiment directory.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "ExperimentRecords" / "dcc_analysis_20260930"
OUTPUT_ROOT = ROOT / "ExperimentRecords" / "dcc_unified_audit_20261002"


SOURCES = {
    "main": SOURCE_ROOT / "analysis" / "main_method_metrics.csv",
    "bandwidth_measured": SOURCE_ROOT
    / "ExperimentRecords"
    / "dcc_bandwidth_sensitivity_measured_10_100_same128"
    / "method_metrics_10_100mbps.csv",
    "ablation": SOURCE_ROOT / "analysis" / "ablation_method_metrics.csv",
    "scp": SOURCE_ROOT / "analysis" / "scp_metrics.csv",
    "mlcomp_e2e": SOURCE_ROOT
    / "ExperimentRecords"
    / "dcc_mlcomp_like_e2e_20261002"
    / "method_metrics.csv",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def summarize(name: str, path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    key_fields = ["bandwidth_mbps", "dataset", "repeat", "method"]
    keys = [tuple(row.get(field, "") for field in key_fields) for row in rows]
    cpss_actions = Counter(
        f"{row.get('selected_action', '')}|{row.get('selected_pipeline', '')}"
        for row in rows
        if row.get("method") == "cpss-adaptive"
    )
    bytes_by_dataset: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        size = integer(row.get("raw_bytes") or row.get("source_bytes"))
        if size is not None:
            bytes_by_dataset[row.get("dataset", "")].append(size)
    return {
        "name": name,
        "path": str(path),
        "sha256": sha256(path),
        "rows": len(rows),
        "unique_keys": len(set(keys)),
        "duplicate_keys": len(keys) - len(set(keys)),
        "status_counts": dict(Counter(row.get("status", "") for row in rows)),
        "methods": dict(Counter(row.get("method", "") for row in rows)),
        "datasets": sorted({row.get("dataset", "") for row in rows}),
        "bandwidths_mbps": sorted(
            {row.get("bandwidth_mbps", "") for row in rows if row.get("bandwidth_mbps", "")}
        ),
        "cpss_actions": dict(cpss_actions),
        "raw_bytes_by_dataset": {
            dataset: sorted(set(values)) for dataset, values in sorted(bytes_by_dataset.items())
        },
        "fields": list(rows[0]) if rows else [],
    }


def load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    summaries = []
    missing = []
    for name, path in SOURCES.items():
        if not path.exists():
            missing.append(str(path))
            continue
        summaries.append(summarize(name, path))

    config_paths = [
        SOURCE_ROOT / "ExperimentRecords" / "dcc_bandwidth_sensitivity_measured_10_100_same128" / "10mbps" / "run" / "runner_config.json",
        SOURCE_ROOT / "ExperimentRecords" / "dcc_bandwidth_sensitivity_measured_10_100_same128" / "100mbps" / "run" / "runner_config.json",
        SOURCE_ROOT / "ExperimentRecords" / "dcc_mlcomp_like_e2e_20261002" / "runner_config.json",
        SOURCE_ROOT / "ExperimentRecords" / "dcc_remaining_supplementary_20260929" / "ablation_run" / "runner_config.json",
        SOURCE_ROOT / "analysis" / "code_hashes.json",
    ]
    configs = {}
    for path in config_paths:
        value = load_json_if_exists(path)
        configs[str(path)] = value if value is not None else {"missing": True}

    # These flags are intentionally conservative.  A missing configuration is
    # reported as an audit gap rather than silently treated as compatible.
    main_summary = next((item for item in summaries if item["name"] == "main"), None)
    bandwidth_summary = next((item for item in summaries if item["name"] == "bandwidth_measured"), None)
    audit_gaps = []
    if main_summary is not None and not main_summary["bandwidths_mbps"]:
        audit_gaps.append("main CSV has no explicit bandwidth_mbps column")
    if main_summary is not None and main_summary["cpss_actions"] != {"compress|zstd": 32, "raw|raw": 19}:
        audit_gaps.append("main CPSS action counts differ from the recorded analysis report")
    if bandwidth_summary is not None and set(bandwidth_summary["bandwidths_mbps"]) != {"10", "100"}:
        audit_gaps.append("measured bandwidth CSV is not exactly the 10/100 Mbps pair")
    audit_gaps.append("main run-level runner_config.json is not present in the canonical analysis directory")
    audit_gaps.append("sampling unit must be verified from code and calibration_sample_indices before new budget runs")

    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(SOURCE_ROOT),
        "output_root": str(OUTPUT_ROOT),
        "summaries": summaries,
        "missing_sources": missing,
        "configs": configs,
        "audit_gaps": audit_gaps,
        "reuse_policy": {
            "main": "primary 100 Mbps end-to-end result",
            "bandwidth_measured": "separate 10/100 Mbps same-input sensitivity result",
            "ablation": "adaptive vs raw/fixed-Zstd paired result; not independent forced-compression evidence",
            "scp": "within-tool cross-check only; do not compare absolute seconds with GridFlux",
            "mlcomp_e2e": "small pilot/appendix until a common-candidate CR selector is measured",
        },
    }
    (OUTPUT_ROOT / "config_audit.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # A compact, human-readable inventory for the paper revision workflow.
    lines = [
        "# Unified DCC experiment audit",
        "",
        f"Created: {report['created_at_utc']}",
        "",
        "## Source inventory",
        "",
        "| source | rows | unique keys | duplicate keys | methods | bandwidths | CPSS actions |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for item in summaries:
        lines.append(
            "| {name} | {rows} | {unique_keys} | {duplicate_keys} | {methods} | {bandwidths} | {actions} |".format(
                name=item["name"],
                rows=item["rows"],
                unique_keys=item["unique_keys"],
                duplicate_keys=item["duplicate_keys"],
                methods=", ".join(sorted(item["methods"])),
                bandwidths=", ".join(item["bandwidths_mbps"]) or "not recorded",
                actions="; ".join(f"{key}={value}" for key, value in item["cpss_actions"].items()) or "n/a",
            )
        )
    lines.extend(["", "## Audit gaps", ""])
    lines.extend(f"- {gap}" for gap in audit_gaps)
    lines.extend(["", "## Reuse policy", ""])
    lines.extend(f"- **{key}**: {value}" for key, value in report["reuse_policy"].items())
    (OUTPUT_ROOT / "audit_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output_root": str(OUTPUT_ROOT), "sources": len(summaries), "audit_gaps": len(audit_gaps)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

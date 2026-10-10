"""Recompute submission evidence from run-level records; never modify source data."""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from analyze_dcc_paper_dataset import audit_package


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "ExperimentRecords/dcc_paper_dataset_20261004"
PAPER = ROOT / "docs/dcc_latex/cpss_submission"


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def medians(rows, field="T_ready_s"):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["dataset"], row["method"])].append(float(row[field]))
    return {key: median(values) for key, values in groups.items()}


def main():
    report = {"source_package": audit_package(PACKAGE), "experiments": {}}
    filenames = {
        "main": "legacy_main_method_metrics.csv",
        "codec_pool": "codec_pool_method_metrics.csv",
        "sampling": "sampling_budget_method_metrics.csv",
        "bandwidth": "bandwidth_sensitivity_method_metrics.csv",
        "raw_bypass": "raw_fallback_method_metrics.csv",
        "ratio_transfer": "legacy_mlcomp_like_e2e_method_metrics.csv",
        "scp": "legacy_scp_cross_validation.csv",
    }
    loaded = {}
    for name, filename in filenames.items():
        rows = loaded[name] = read_csv(PACKAGE / "derived" / filename)
        keys = [(r.get("run_id"), r["dataset"], r["repeat"], r["method"],
                 r.get("sampling_budget"), r.get("bandwidth_mbps")) for r in rows]
        failed = [r for r in rows if r["status"] != "completed"
                  or r["roundtrip_ok"].lower() != "true"]
        report["experiments"][name] = {
            "rows": len(rows), "unique_keys": len(set(keys)),
            "failed_status_or_integrity": len(failed),
            "actions": dict(Counter(r.get("selected_action", r.get("payload_mode"))
                                    for r in rows if r["method"] == "cpss-adaptive")),
        }
        assert len(keys) == len(set(keys)), name
        assert not failed, name

    values = medians(loaded["main"])
    datasets = sorted({d for d, m in values})
    peak_rows = [{"dataset": d, "raw_s": values[d, "raw"],
                  "zstd_s": values[d, "zstd-3"], "cpss_s": values[d, "cpss-adaptive"],
                  "speedup_vs_raw": values[d, "raw"] / values[d, "cpss-adaptive"],
                  "reduction_vs_raw_pct": 100 * (1 - values[d, "cpss-adaptive"] / values[d, "raw"]),
                  "reduction_vs_zstd_pct": 100 * (1 - values[d, "cpss-adaptive"] / values[d, "zstd-3"])}
                 for d in datasets]
    report["per_workload"] = peak_rows
    report["peak_raw"] = max(peak_rows, key=lambda r: r["speedup_vs_raw"])
    report["peak_zstd"] = max(peak_rows, key=lambda r: r["reduction_vs_zstd_pct"])
    report["totals"] = {m: sum(v for (d, method), v in values.items() if method == m)
                        for m in sorted({m for d, m in values})}
    cpss = [r for r in loaded["main"] if r["method"] == "cpss-adaptive"]
    report["main_probe"] = {"median_s": median(float(r["probe_time_s"]) for r in cpss),
                            "counts": dict(Counter(r["sample_count"] for r in cpss))}
    groups = defaultdict(list)
    for r in cpss:
        groups[r["dataset"]].append(float(r["T_ready_s"]))
    spreads = [(max(v) - min(v)) / median(v) * 100 for v in groups.values()]
    report["main_spread"] = {"median_pct": median(spreads), "max_pct": max(spreads)}
    report["branches"] = {}
    for action in ["raw", "compress"]:
        ds = {r["dataset"] for r in cpss if r["selected_action"] == action}
        totals = {m: sum(values[d, m] for d in ds) for m in ["raw", "zstd-3", "cpss-adaptive"]}
        report["branches"][action] = {"datasets": sorted(ds), "totals": totals,
            "over_raw_pct": 100 * (totals["cpss-adaptive"] / totals["raw"] - 1),
            "over_zstd_pct": 100 * (totals["cpss-adaptive"] / totals["zstd-3"] - 1)}
    report["input_gib"] = {d: int(next(r["raw_bytes"] for r in cpss if r["dataset"] == d)) / 2**30
                           for d in datasets}

    # Independently reconcile the figure's legacy summary with the run-level package.
    figure_rows = read_csv(ROOT / "ExperimentRecords/dcc_analysis_20260930/analysis/main_method_summary.csv")
    for r in figure_rows:
        assert math.isclose(report["totals"][r["method"]], float(r["median_sum_T_ready_s"]), abs_tol=1e-7)
    pairwise = read_csv(ROOT / "ExperimentRecords/dcc_analysis_20260930/analysis/dataset_pairwise_summary.csv")
    for r in pairwise:
        expected = next(v for v in peak_rows if v["dataset"] == r["dataset"])
        assert math.isclose(expected["reduction_vs_raw_pct"], float(r["cpss_vs_raw_time_reduction_pct"]), abs_tol=1e-7)

    report["sampling"] = []
    for cap in ["1", "2", "5"]:
        rows = [r for r in loaded["sampling"] if r["sampling_budget"] == cap]
        by_key = {(r["dataset"], r["repeat"], r["method"]): r for r in rows}
        cp = [r for r in rows if r["method"] == "cpss-adaptive"]
        agreement, excess = [], []
        for r in cp:
            raw = float(by_key[r["dataset"], r["repeat"], "raw"]["T_ready_s"])
            zstd = float(by_key[r["dataset"], r["repeat"], "zstd-3"]["T_ready_s"])
            best = "raw" if raw <= zstd else "compress"
            agreement.append(r["selected_action"] == best)
            excess.append(100 * (float(r["T_ready_s"]) / min(raw, zstd) - 1))
        report["sampling"].append({"cap": cap, "pairs": len(cp), "matches": sum(agreement),
            "median_excess_pct": median(excess), "median_probe_s": median(float(r["probe_time_s"]) for r in cp),
            "window_counts": dict(Counter(r["sample_count"] for r in cp))})

    model = json.loads((PACKAGE / "raw/server_20261004/dcc_mlcomp_like_20261004/mlcomp_like_small.json").read_text())
    report["ratio_selector"] = {"selected_features": model["selected_features"],
        "test_result": model["test_selected_features"],
        "probe_s": {r["dataset"]: r["probe_s"] for r in model["rows"] if r["split"] == "test"}}
    for name in ["codec_pool", "raw_bypass", "ratio_transfer"]:
        report["experiments"][name]["medians_s"] = {f"{d}/{m}": v for (d, m), v in medians(loaded[name]).items()}

    bibkeys = set(re.findall(r"@\w+\{([^,]+),", (PAPER / "refs.bib").read_text(encoding="utf-8")))
    report["manuscripts"] = {}
    for name in ["main", "main_zh"]:
        source = (PAPER / f"{name}.tex").read_text(encoding="utf-8")
        citations = {key.strip() for group in re.findall(r"\\cite\{([^}]+)\}", source) for key in group.split(",")}
        labels = re.findall(r"\\label\{([^}]+)\}", source)
        refs = re.findall(r"\\ref\{([^}]+)\}", source)
        figures = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", source)
        abstract = source.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0]
        report["manuscripts"][name] = {
            "missing_citations": sorted(citations - bibkeys),
            "citation_count": len(citations),
            "missing_labels": sorted(set(refs) - set(labels)),
            "duplicate_labels": [k for k, v in Counter(labels).items() if v > 1],
            "missing_figures": [f for f in figures if not (PAPER / f).is_file()],
            "abstract_space_separated_words": len(abstract.split()),
            "author_placeholder": "to be inserted" in source or "待填写" in source,
            "obsolete_claims": re.findall(r"Our DCC 2024|our DCC 2024|our MLcomp|1\s*Gbps|冻结", source),
        }
        checks = report["manuscripts"][name]
        assert not any(checks[k] for k in ["missing_citations", "missing_labels", "duplicate_labels", "missing_figures", "obsolete_claims"])
    assert not report["source_package"]["manifest_mismatches"]
    assert report["peak_raw"]["dataset"] == "noaa_gsod_csv"
    assert report["peak_zstd"]["dataset"] == "nyx"
    output = PAPER / "tmp/pdf_qa_20261009/submission_evidence_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ["peak_raw", "peak_zstd", "branches", "sampling", "main_spread", "manuscripts"]}, indent=2))
    print(f"Audit passed; evidence saved to {output}")


if __name__ == "__main__":
    main()

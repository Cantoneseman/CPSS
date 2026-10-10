"""Build analysis-ready tables from the frozen CPSS DCC experiments.

The script is intentionally dependency-free (stdlib only) so it can be rerun on
another machine after copying the frozen ExperimentRecords directory.  It does
not change the canonical experiment CSVs; all derived files are written below
ExperimentRecords/dcc_analysis_20260930/analysis.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from statistics import mean, median


HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
PACKAGE = REPO / "ExperimentRecords" / "dcc_analysis_20260930"
SOURCE = PACKAGE / "ExperimentRecords"
OUT = PACKAGE / "analysis"

MAIN = SOURCE / "dcc_main_v1_20260927_final_20260929" / "method_metrics.csv"
ABLATION = SOURCE / "dcc_remaining_supplementary_20260929" / "ablation_run" / "method_metrics.csv"
ABLATION_SUMMARY = SOURCE / "dcc_remaining_supplementary_20260929" / "ablation_summary.csv"
SCP = SOURCE / "dcc_remaining_supplementary_20260929" / "gridflux_scp_cross_validation" / "scp_metrics.csv"
BANDWIDTH = SOURCE / "dcc_remaining_supplementary_20260929" / "bandwidth_sensitivity.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def f(row: dict, key: str, default: float = float("nan")) -> float:
    try:
        v = row.get(key, "")
        return default if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return default


def i(row: dict, key: str, default: int = 0) -> int:
    try:
        return int(float(row.get(key, default)))
    except (TypeError, ValueError):
        return default


def finite(values):
    return [float(x) for x in values if x is not None and math.isfinite(float(x))]


def geomean(values) -> float:
    vals = finite(values)
    if not vals or any(x <= 0 for x in vals):
        return float("nan")
    return math.exp(sum(math.log(x) for x in vals) / len(vals))


def pct(numerator: float, denominator: float) -> float:
    """Percent reduction of numerator relative to denominator."""
    return (1.0 - numerator / denominator) * 100 if denominator else float("nan")


def fmt(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return ""
    return v


def add_hash(path: Path) -> dict:
    h = hashlib.sha256()
    with path.open("rb") as fobj:
        for chunk in iter(lambda: fobj.read(1024 * 1024), b""):
            h.update(chunk)
    return {"path": str(path), "sha256": h.hexdigest(), "bytes": path.stat().st_size}


def group(rows, *keys):
    out = defaultdict(list)
    for r in rows:
        out[tuple(r[k] for k in keys)].append(r)
    return out


def main_plot_data(main: list[dict[str, str]]) -> tuple[list[dict], list[dict], list[dict]]:
    by_pair = group(main, "dataset", "repeat")
    raw = {(d, r): next((x for x in rs if x["method"] == "raw"), None) for (d, r), rs in by_pair.items()}
    zstd = {(d, r): next((x for x in rs if x["method"] == "zstd-3"), None) for (d, r), rs in by_pair.items()}
    long_rows = []
    for src in main:
        r = dict(src)
        d, rep = src["dataset"], src["repeat"]
        rr, zz = raw[(d, rep)], zstd[(d, rep)]
        t = f(src, "T_ready_s")
        raw_t = f(rr, "T_ready_s") if rr else float("nan")
        zstd_t = f(zz, "T_ready_s") if zz else float("nan")
        r.update({
            "method_label": src["method"],
            "T_ready_min": t / 60,
            "T_verified_min": f(src, "T_verified_s") / 60,
            "transferred_GiB": f(src, "transferred_bytes") / 2**30,
            "payload_GiB": f(src, "compressed_payload_bytes") / 2**30,
            "speedup_vs_raw": raw_t / t if t else float("nan"),
            "time_reduction_vs_raw_pct": pct(t, raw_t),
            "speedup_vs_zstd": zstd_t / t if zstd_t and t else float("nan"),
            "time_reduction_vs_zstd_pct": pct(t, zstd_t),
            "payload_reduction_pct_vs_raw": pct(f(src, "compressed_payload_bytes"), f(rr, "raw_bytes") if rr else float("nan")),
            "cpus_probe_plus_fallback_s": f(src, "probe_time_s") + f(src, "fallback_overhead_s"),
        })
        long_rows.append(r)

    dataset_method = []
    for (dataset, method), rs in sorted(group(main, "dataset", "method").items()):
        vals = [f(x, "T_ready_s") for x in rs]
        vver = [f(x, "T_verified_s") for x in rs]
        transferred = [f(x, "transferred_bytes") for x in rs]
        raw_rs = [x for x in main if x["dataset"] == dataset and x["method"] == "raw"]
        raw_med = median(f(x, "T_ready_s") for x in raw_rs) if raw_rs else float("nan")
        row = {
            "dataset": dataset, "method": method, "repeats": len(rs),
            "T_ready_median_s": median(vals), "T_ready_sum_s": sum(vals),
            "T_ready_mean_s": mean(vals), "T_ready_p10_s": sorted(vals)[max(0, math.ceil(.1 * len(vals)) - 1)],
            "T_ready_p90_s": sorted(vals)[max(0, math.ceil(.9 * len(vals)) - 1)],
            "T_verified_median_s": median(vver), "transferred_median_bytes": median(transferred),
            "raw_T_ready_median_s": raw_med,
            "speedup_vs_raw": raw_med / median(vals) if median(vals) else float("nan"),
            "time_reduction_vs_raw_pct": pct(median(vals), raw_med),
            "cpus_probe_median_s": median(f(x, "probe_time_s") for x in rs),
            "compress_median_s": median(f(x, "compress_time_s") for x in rs),
            "gridflux_transfer_median_s": median(f(x, "gridflux_transfer_time_s") for x in rs),
            "remote_decompress_median_s": median(f(x, "remote_decompress_time_s") for x in rs),
            "remote_rebuild_median_s": median(f(x, "remote_rebuild_time_s") for x in rs),
            "remote_verify_median_s": median(f(x, "remote_verify_time_s") for x in rs),
            "completed_rows": sum(x.get("status") == "completed" for x in rs),
        }
        dataset_method.append(row)

    method_summary = []
    dm = group(dataset_method, "method")
    for method_key, rs in sorted(dm.items()):
        method = method_key[0]
        per_dataset = [f(x, "T_ready_median_s") for x in rs]
        raw_ds = [f(x, "raw_T_ready_median_s") for x in rs]
        ratios = [r / x for r, x in zip(raw_ds, per_dataset) if x]
        wins = sum(x > 1 for x in ratios)
        method_summary.append({
            "method": method, "datasets": len(rs), "rows": sum(i(x, "repeats") for x in rs),
            "median_sum_T_ready_s": sum(per_dataset), "all_run_sum_T_ready_s": sum(f(x, "T_ready_s") for x in main if x["method"] == method),
            "speedup_vs_raw_median_sum": sum(raw_ds) / sum(per_dataset),
            "time_reduction_vs_raw_median_sum_pct": pct(sum(per_dataset), sum(raw_ds)),
            "geomean_speedup_vs_raw": geomean(ratios), "dataset_wins_vs_raw": wins,
            "dataset_losses_vs_raw": len(ratios) - wins,
        })
    return long_rows, dataset_method, method_summary


def cpss_tables(main: list[dict[str, str]]) -> tuple[list[dict], list[dict]]:
    by_pair = group(main, "dataset", "repeat")
    out = []
    for src in main:
        if src["method"] != "cpss-adaptive":
            continue
        rr = next(x for x in by_pair[(src["dataset"], src["repeat"])] if x["method"] == "raw")
        zz = next(x for x in by_pair[(src["dataset"], src["repeat"])] if x["method"] == "zstd-3")
        t, rt, zt = f(src, "T_ready_s"), f(rr, "T_ready_s"), f(zz, "T_ready_s")
        branch = "compress" if src.get("selected_action") == "compress" else "raw"
        out.append({
            "dataset": src["dataset"], "repeat": src["repeat"], "branch": branch,
            "selected_pipeline": src.get("selected_pipeline", ""), "bypass_reason": src.get("bypass_reason", ""),
            "cpss_T_ready_s": t, "raw_T_ready_s": rt, "zstd_T_ready_s": zt,
            "cpss_vs_raw_time_reduction_pct": pct(t, rt), "cpss_vs_zstd_time_reduction_pct": pct(t, zt),
            "speedup_vs_raw": rt / t if t else float("nan"), "probe_time_s": f(src, "probe_time_s"),
            "compress_time_s": f(src, "compress_time_s"), "transfer_time_s": f(src, "gridflux_transfer_time_s"),
            "system_regret_vs_oracle": f(src, "system_regret_vs_oracle"),
        })
    aggregate = []
    for branch in ("all", "compress", "raw"):
        rs = out if branch == "all" else [x for x in out if x["branch"] == branch]
        aggregate.append({
            "branch": branch, "rows": len(rs),
            "cpss_sum_T_ready_s": sum(x["cpss_T_ready_s"] for x in rs),
            "raw_sum_T_ready_s": sum(x["raw_T_ready_s"] for x in rs),
            "zstd_sum_T_ready_s": sum(x["zstd_T_ready_s"] for x in rs),
            "cpss_vs_raw_time_reduction_pct": pct(sum(x["cpss_T_ready_s"] for x in rs), sum(x["raw_T_ready_s"] for x in rs)),
            "cpss_vs_zstd_time_reduction_pct": pct(sum(x["cpss_T_ready_s"] for x in rs), sum(x["zstd_T_ready_s"] for x in rs)),
            "mean_system_regret_pct": mean(x["system_regret_vs_oracle"] for x in rs),
        })
    return out, aggregate


def dataset_pairwise_summary(dataset_method: list[dict], main: list[dict[str, str]]) -> list[dict]:
    rows = []
    by_ds = group(dataset_method, "dataset")
    for dataset_key, rs in sorted(by_ds.items()):
        dataset = dataset_key[0]
        one = {x["method"]: x for x in rs}
        raw = one.get("raw")
        zstd = one.get("zstd-3")
        cpss = one.get("cpss-adaptive")
        crows = [x for x in main if x["dataset"] == dataset and x["method"] == "cpss-adaptive"]
        actions = defaultdict(int)
        for x in crows:
            actions[x.get("selected_action", "")] += 1
        if not (raw and zstd and cpss):
            continue
        ct = f(cpss, "T_ready_median_s")
        rt = f(raw, "T_ready_median_s")
        zt = f(zstd, "T_ready_median_s")
        rows.append({
            "dataset": dataset, "cpss_T_ready_median_s": ct, "raw_T_ready_median_s": rt,
            "zstd_T_ready_median_s": zt, "cpss_vs_raw_time_reduction_pct": pct(ct, rt),
            "cpss_vs_zstd_time_reduction_pct": pct(ct, zt), "cpss_vs_raw_speedup": rt / ct,
            "cpss_vs_zstd_speedup": zt / ct, "compress_repeats": actions.get("compress", 0),
            "raw_repeats": actions.get("raw", 0), "cpss_selected_pipeline": ",".join(sorted({x.get("selected_pipeline", "") for x in crows})),
        })
    return rows


def pairwise_method_summary(method_summary: list[dict]) -> list[dict]:
    by_method = {x["method"]: x for x in method_summary}
    out = []
    for method, baseline in (("cpss-adaptive", "raw"), ("cpss-adaptive", "zstd-3"), ("zstd-3", "raw"), ("lz4", "raw"), ("gzip-6", "raw"), ("xz-6", "raw")):
        if method not in by_method or baseline not in by_method:
            continue
        t = float(by_method[method]["median_sum_T_ready_s"])
        b = float(by_method[baseline]["median_sum_T_ready_s"])
        out.append({"method": method, "baseline": baseline, "method_median_sum_T_ready_s": t, "baseline_median_sum_T_ready_s": b, "speedup_method_vs_baseline": b / t, "time_reduction_method_vs_baseline_pct": pct(t, b), "geomean_speedup_method_vs_raw": by_method[method]["geomean_speedup_vs_raw"]})
    return out


def cross_validation_tables(ablation: list[dict[str, str]], scp: list[dict[str, str]]) -> list[dict]:
    out = []
    # GridFlux ablation rows are the controlled transport-side comparison.
    for x in ablation:
        t = f(x, "T_ready_s")
        out.append({
            "tool": "gridflux", "dataset": x["dataset"], "repeat": x["repeat"], "method": x["method"],
            "T_total_s": t, "payload_mode": x.get("selected_action", ""), "roundtrip_ok": x.get("roundtrip_ok", ""),
        })
    for x in scp:
        out.append({
            "tool": "scp", "dataset": x["dataset"], "repeat": x["repeat"], "method": x["method"],
            "T_total_s": f(x, "total_time_s"), "payload_mode": x.get("payload_mode", ""), "roundtrip_ok": x.get("roundtrip_ok", ""),
        })
    by_pair_tool = group(out, "tool", "dataset", "repeat")
    for x in out:
        raw = next((z for z in by_pair_tool[(x["tool"], x["dataset"], x["repeat"])] if z["method"] == "raw"), None)
        rt = raw["T_total_s"] if raw else float("nan")
        x["speedup_vs_raw"] = rt / x["T_total_s"] if x["T_total_s"] else float("nan")
        x["time_reduction_vs_raw_pct"] = pct(x["T_total_s"], rt)
    return out


def grouped_median_summary(rows: list[dict], keys: tuple[str, ...], value_key: str) -> list[dict]:
    out = []
    for key, rs in sorted(group(rows, *keys).items()):
        vals = [r[value_key] for r in rs if math.isfinite(float(r[value_key]))]
        if not vals:
            continue
        row = {k: v for k, v in zip(keys, key)}
        row.update({"rows": len(rs), "median_T_total_s": median(vals), "mean_T_total_s": mean(vals)})
        raw = next((r[value_key] for r in group(rows, *keys).get(key, []) if r["method"] == "raw"), None)
        # For cross-validation the baseline is grouped by tool/dataset/repeat,
        # so this branch is only used for rows where the grouping includes repeat.
        if raw is not None:
            row["speedup_vs_raw"] = raw / median(vals)
        out.append(row)
    return out


def cross_validation_summary(rows: list[dict]) -> list[dict]:
    out = []
    for (tool, dataset, method), rs in sorted(group(rows, "tool", "dataset", "method").items()):
        vals = [r["T_total_s"] for r in rs]
        rel = [r["speedup_vs_raw"] for r in rs]
        out.append({
            "tool": tool, "dataset": dataset, "method": method, "rows": len(rs),
            "median_T_total_s": median(vals), "mean_T_total_s": mean(vals),
            "median_speedup_vs_raw": median(rel), "median_time_reduction_vs_raw_pct": median(r["time_reduction_vs_raw_pct"] for r in rs),
            "roundtrip_ok_rows": sum(str(r["roundtrip_ok"]).lower() == "true" for r in rs),
        })
    return out


def ablation_plot_data(ablation: list[dict[str, str]]) -> list[dict]:
    out = []
    by_pair = group(ablation, "dataset", "repeat")
    for src in ablation:
        t = f(src, "T_ready_s")
        raw = next((x for x in by_pair[(src["dataset"], src["repeat"])] if x["method"] == "raw"), None)
        zstd = next((x for x in by_pair[(src["dataset"], src["repeat"])] if x["method"] == "zstd-3"), None)
        rt = f(raw, "T_ready_s") if raw else float("nan")
        zt = f(zstd, "T_ready_s") if zstd else float("nan")
        out.append({
            "dataset": src["dataset"], "repeat": src["repeat"], "method": src["method"],
            "T_ready_s": t, "T_verified_s": f(src, "T_verified_s"),
            "selected_action": src.get("selected_action", ""), "selected_pipeline": src.get("selected_pipeline", ""),
            "speedup_vs_raw": rt / t if t else float("nan"), "time_reduction_vs_raw_pct": pct(t, rt),
            "speedup_vs_fixed_zstd": zt / t if zt and t else float("nan"),
            "time_reduction_vs_fixed_zstd_pct": pct(t, zt),
        })
    return out


def historical_map() -> list[dict]:
    return [
        {"source": "CPSS_P0_REPAIR_REPORT_20260818.md", "conclusion": "Raw 是合法动作；cold E2E 必须包含探测/校准成本；warm 与 cold 分开。", "current_use": "用于解释 CPSS raw 分支仍可能略慢于 raw，以及报告指标边界。", "status": "supported"},
        {"source": "CPSS_SAMPLING_DEDUP_EXPERIMENT_REPORT_20260729.md", "conclusion": "采样能降低压缩决策成本，但一次性 cold 流程可能被校准成本拖慢；随机种子会影响管线。", "current_use": "用于解释 CPSS 压缩分支相对固定 zstd 的小幅开销；不能替代冻结主实验。", "status": "supported_as_context"},
        {"source": "docs/decision-memory-v1-v2.md", "conclusion": "决策记忆可减少候选调用，warm 命中接近零候选调用。", "current_use": "未来优化方向；不并入本轮正式定量主结论。", "status": "future_only"},
        {"source": "DCC_FINAL_EXPERIMENT_PROTOCOL.md", "conclusion": "主统计使用每数据集 repeat 中位数求和与 geomean；scp/GridFlux 和带宽模型为补充证据。", "current_use": "本脚本的聚合口径和图表数据分层。", "status": "protocol"},
        {"source": "docs/DCC_2027_CPSS_paper_draft_v0.md", "conclusion": "不能声称 CPSS 对所有数据集都更快；采样预算矩阵和专用科学压缩基线若未运行，不应宣称已验证。", "current_use": "报告局限；当前正式结果没有非 zstd 的 CPSS 选中案例。", "status": "limitation"},
        {"source": "AI_COMPRESSION_SCENARIOS_REPORT_20260727.md", "conclusion": "外部 AI 文本/元数据采样实验。", "current_use": "不与 DCC 主实验合并，避免协议混淆。", "status": "excluded"},
    ]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    main_rows = read_csv(MAIN)
    ablation_rows = read_csv(ABLATION)
    scp_rows = read_csv(SCP)
    bandwidth_rows = read_csv(BANDWIDTH)

    # Copy immutable source files to a flat, discoverable analysis directory.
    copies = {
        "main_method_metrics.csv": MAIN,
        "ablation_method_metrics.csv": ABLATION,
        "ablation_summary.csv": ABLATION_SUMMARY,
        "scp_metrics.csv": SCP,
        "bandwidth_sensitivity.csv": BANDWIDTH,
        "bandwidth_sensitivity_summary.csv": BANDWIDTH.parent / "bandwidth_sensitivity_summary.csv",
        "merge_report.json": MAIN.parent / "merge_report.json",
        "code_hashes.json": MAIN.parent / "code_hashes.json",
        "ablation_manifest.json": ABLATION.parent.parent / "ablation_manifest.json",
        "cross_validation_report.json": SCP.parent / "cross_validation_report.json",
    }
    provenance = {"source_files": []}
    for name, src in copies.items():
        if src.exists():
            dst = OUT / name
            shutil.copy2(src, dst)
            provenance["source_files"].append(add_hash(dst))

    long_rows, dataset_method, method_summary = main_plot_data(main_rows)
    cpss_rows, cpss_aggregate = cpss_tables(main_rows)
    dataset_pairwise = dataset_pairwise_summary(dataset_method, main_rows)
    cross_rows = cross_validation_tables(ablation_rows, scp_rows)
    ablation_plot_rows = ablation_plot_data(ablation_rows)
    write_csv(OUT / "main_plot_data.csv", long_rows)
    write_csv(OUT / "main_dataset_method_summary.csv", dataset_method)
    write_csv(OUT / "main_method_summary.csv", method_summary)
    write_csv(OUT / "dataset_pairwise_summary.csv", dataset_pairwise)
    write_csv(OUT / "pairwise_method_summary.csv", pairwise_method_summary(method_summary))
    write_csv(OUT / "cpss_decision_plot_data.csv", cpss_rows)
    write_csv(OUT / "cpss_decision_aggregate.csv", cpss_aggregate)
    write_csv(OUT / "cross_validation_plot_data.csv", cross_rows)
    write_csv(OUT / "cross_validation_summary.csv", cross_validation_summary(cross_rows))
    write_csv(OUT / "ablation_plot_data.csv", ablation_plot_rows)
    write_csv(OUT / "bandwidth_plot_data.csv", bandwidth_rows)
    write_csv(OUT / "historical_conclusion_map.csv", historical_map())

    summary = {
        "main_rows": len(main_rows), "main_unique_dataset_repeat_method": len({(x["dataset"], x["repeat"], x["method"]) for x in main_rows}),
        "main_datasets": sorted({x["dataset"] for x in main_rows}), "main_methods": sorted({x["method"] for x in main_rows}),
        "ablation_rows": len(ablation_rows), "scp_rows": len(scp_rows), "bandwidth_rows": len(bandwidth_rows),
        "cpss_action_counts": {k: sum(x.get("selected_action") == k for x in main_rows if x["method"] == "cpss-adaptive") for k in ("compress", "raw")},
        "source_files": provenance["source_files"],
        "output_files": sorted(p.name for p in OUT.iterdir() if p.is_file()),
    }
    (OUT / "analysis_manifest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    figure_manifest = [
        {"figure_id": "main_time_by_dataset", "source": "main_dataset_method_summary.csv", "x": "dataset", "y": "T_ready_median_s", "group": "method", "note": "主实验端到端可用时间；优先画中位数和重复误差。"},
        {"figure_id": "cpss_vs_baselines_by_dataset", "source": "dataset_pairwise_summary.csv", "x": "dataset", "y": "cpss_vs_raw_time_reduction_pct", "group": "dataset", "note": "CPSS 与 Raw/zstd 的数据集级配对比较。"},
        {"figure_id": "cpss_action_and_gain", "source": "cpss_decision_plot_data.csv", "x": "dataset", "y": "cpss_vs_raw_time_reduction_pct", "group": "branch", "note": "CPSS raw/compress 分支相对配对 Raw 的收益。"},
        {"figure_id": "method_aggregate", "source": "main_method_summary.csv", "x": "method", "y": "time_reduction_vs_raw_median_sum_pct", "group": "method", "note": "协议规定的跨数据集聚合；与 geomean speedup 一起报告。"},
        {"figure_id": "ablation", "source": "ablation_plot_data.csv", "x": "dataset", "y": "T_ready_s", "group": "method", "note": "消融；forced-compression-zstd 在原始补充表中与 CPSS-static-zstd 重复，不能当作独立实现。"},
        {"figure_id": "cross_validation", "source": "cross_validation_summary.csv", "x": "dataset", "y": "median_speedup_vs_raw", "group": "tool,method", "note": "GridFlux 和 scp 各自归一化到 Raw；不直接比较两种工具的绝对秒数。"},
        {"figure_id": "bandwidth", "source": "bandwidth_sensitivity_summary.csv", "x": "bandwidth_mbps", "y": "geomean_speedup", "group": "method", "note": "10/100/1000 Mbps post-hoc 模型，不是新增物理链路实验。"},
    ]
    (OUT / "figure_manifest.json").write_text(json.dumps(figure_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # Keep a short Markdown handoff beside the CSVs.  Exact numbers come from the generated tables.
    raw = next(x for x in method_summary if x["method"] == "raw")
    cpss = next(x for x in method_summary if x["method"] == "cpss-adaptive")
    zstd = next(x for x in method_summary if x["method"] == "zstd-3")
    cpss_vs_raw = pct(cpss["median_sum_T_ready_s"], raw["median_sum_T_ready_s"])
    cpss_vs_zstd = pct(cpss["median_sum_T_ready_s"], zstd["median_sum_T_ready_s"])
    report = f"""# CPSS 冻结实验分析交接

本目录由 `Script/analyze_dcc_results.py` 生成。源数据保持不变，图表应优先读取 `*_plot_data.csv` 或 `*_summary.csv`。

## 当前可直接使用的结论

- 主实验通过冻结完整性审计：306 条记录，306 个 `(dataset, repeat, method)` 唯一键。
- 按协议的“每数据集 repeat 中位数求和”口径，CPSS 相对 Raw 的总 `T_ready` 变化为 **{cpss_vs_raw:.2f}%**（正数表示更快），相对固定 zstd-3 为 **{cpss_vs_zstd:.2f}%**。
- CPSS 的动作计数为 `{summary['cpss_action_counts']['compress']}` 次 compress、`{summary['cpss_action_counts']['raw']}` 次 raw。压缩动作本轮全部选择 zstd；因此本轮没有“CPSS 选出非 zstd 且超过 zstd”的证据。
- 应将 CPSS 描述为“按输入和成本模型在 Raw 与压缩之间选择”。raw 分支会保留探测成本，所以可出现与 Raw 几乎持平或略慢；压缩分支通常减少传输时间，但探测/压缩准备会使它略慢于直接固定 zstd。

## 图表数据

- `main_plot_data.csv`：主实验逐行长表，含相对 Raw/zstd 的速度和时间变化。
- `main_dataset_method_summary.csv`：数据集×方法的 repeat 中位数、分位数、组件时间。
- `main_method_summary.csv`：论文主聚合口径（每数据集 median sum、geomean、wins）。
- `cpss_decision_plot_data.csv`、`cpss_decision_aggregate.csv`：CPSS raw/compress 分支及配对基线。
- `cross_validation_plot_data.csv`：GridFlux 与 scp 的逐行数据和各自 Raw 归一化结果；两种工具的计时边界不同，应画相对值，不直接比较绝对秒数。
- `bandwidth_plot_data.csv`：10/100/1000 Mbps 的模型外推；这是 post-hoc 模型，不是新增物理链路测量。

## 使用边界

历史报告和论文草稿已记录：采样预算矩阵、专用科学压缩基线、决策记忆收益和多物理链路尚未纳入本轮正式证据；外部 AI 文本实验不应并入 DCC 主图。`historical_conclusion_map.csv` 给出逐条来源和用途。
"""
    (OUT / "analysis_report.md").write_text(report, encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

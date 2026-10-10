"""Generate provisional DCC figures from the formal method_metrics.csv.

The script is intentionally data-driven: replace --csv with the final merged
CSV and rerun it without changing the plotting code or statistical definitions.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import numpy as np


DATASET_ORDER = [
    "cesm_atm",
    "hurricane_isabel",
    "miranda",
    "nyx",
    "hacc",
    "scale_letkf",
    "exafel",
    "rcsb_mmcif",
    "ncbi_grch38_fasta",
    "noaa_gsod_csv",
]
DATASET_LABELS = {
    "cesm_atm": "CESM",
    "hurricane_isabel": "Hurricane",
    "miranda": "Miranda",
    "nyx": "NYX",
    "hacc": "HACC",
    "scale_letkf": "SCALE",
    "exafel": "ExaFEL",
    "rcsb_mmcif": "RCSB",
    "ncbi_grch38_fasta": "NCBI",
    "noaa_gsod_csv": "NOAA",
}

COLORS = {
    "compress": "#0072B2",
    "raw": "#D55E00",
    "pending": "#7A7A7A",
    "probe": "#56B4E9",
    "encode": "#009E73",
    "transfer": "#E69F00",
    "restore": "#CC79A7",
    "neutral": "#666666",
}


def number(row: dict[str, str], key: str) -> float | None:
    try:
        value = float(row.get(key, ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def completed(rows: list[dict[str, str]], method: str | None = None) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("status") == "completed"
        and (method is None or row.get("method") == method)
    ]


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def paired_speedups(rows: list[dict[str, str]], dataset: str) -> list[float]:
    paired: dict[str, dict[str, float]] = defaultdict(dict)
    for row in completed(rows):
        if row.get("dataset") != dataset or row.get("method") not in {"raw", "cpss-adaptive"}:
            continue
        value = number(row, "T_ready_s")
        if value is not None:
            paired[row["repeat"]][row["method"]] = value
    return [v["raw"] / v["cpss-adaptive"] for v in paired.values() if len(v) == 2]


def bootstrap_ci(values: list[float], seed: int = 7, n: int = 5000) -> tuple[float, float] | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    samples = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(n)]
    return float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def save(fig: plt.Figure, output: Path, name: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / f"{name}.pdf", bbox_inches="tight")
    fig.savefig(output / f"{name}.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def figure_architecture(output: Path) -> None:
    fig, ax = plt.subplots(figsize=(10.5, 3.8))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    def box(x: float, y: float, w: float, h: float, text: str, color: str) -> None:
        patch = FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.015",
            linewidth=1.1, edgecolor=color, facecolor="#FFFFFF",
        )
        ax.add_patch(patch)
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=9)

    def arrow(x1: float, y1: float, x2: float, y2: float, text: str | None = None) -> None:
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                     mutation_scale=12, linewidth=1.0, color="#555555"))
        if text:
            ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.035, text,
                    ha="center", va="bottom", fontsize=8, color="#555555")

    box(0.02, 0.40, 0.13, 0.20, "Dataset\ntree", COLORS["neutral"])
    box(0.20, 0.40, 0.14, 0.20, "Scan +\nmanifest", COLORS["probe"])
    box(0.39, 0.62, 0.16, 0.20, "Random windows\n+ candidate probes", COLORS["probe"])
    box(0.39, 0.19, 0.16, 0.20, "Raw action\n(no full encode)", COLORS["raw"])
    box(0.62, 0.62, 0.16, 0.20, "Compressed\nbundle", COLORS["compress"])
    box(0.62, 0.19, 0.16, 0.20, "Original\ntree", COLORS["raw"])
    box(0.84, 0.40, 0.14, 0.20, "GridFlux\ntransfer", COLORS["transfer"])
    box(0.84, 0.04, 0.14, 0.18, "Decode / rebuild\nusable tree", COLORS["restore"])

    arrow(0.15, 0.50, 0.20, 0.50)
    arrow(0.34, 0.53, 0.39, 0.70)
    arrow(0.34, 0.47, 0.39, 0.29)
    arrow(0.55, 0.70, 0.62, 0.70, "beneficial")
    arrow(0.55, 0.29, 0.62, 0.29, "bypass")
    arrow(0.78, 0.70, 0.84, 0.53)
    arrow(0.78, 0.29, 0.84, 0.49)
    arrow(0.91, 0.40, 0.91, 0.22)

    ax.text(0.50, 0.93, "CPSS: cost-aware end-to-end action selection", ha="center",
            va="center", fontsize=11, fontweight="bold")
    ax.text(0.50, -0.01, "T_ready ends after the receiver has a usable reconstructed tree; independent SHA-256 is reported separately.",
            ha="center", va="top", fontsize=8, color="#555555")
    save(fig, output, "fig1_cpss_architecture")


def figure_cpss_vs_raw(rows: list[dict[str, str]], output: Path) -> None:
    summary = []
    for dataset in DATASET_ORDER:
        raw = [number(r, "T_ready_s") for r in completed(rows) if r.get("dataset") == dataset and r.get("method") == "raw"]
        cp = [number(r, "T_ready_s") for r in completed(rows) if r.get("dataset") == dataset and r.get("method") == "cpss-adaptive"]
        raw = [v for v in raw if v is not None]
        cp = [v for v in cp if v is not None]
        speeds = paired_speedups(rows, dataset)
        if speeds:
            action_rows = [r for r in completed(rows, "cpss-adaptive") if r.get("dataset") == dataset]
            actions = Counter(r.get("selected_action") for r in action_rows)
            action = actions.most_common(1)[0][0] if actions else "raw"
            ci = bootstrap_ci(speeds, seed=sum((i + 1) * ord(ch) for i, ch in enumerate(dataset)))
            summary.append((dataset, statistics.median(speeds), ci, action, len(speeds)))
        else:
            summary.append((dataset, None, None, "pending", 0))

    y = np.arange(len(summary))
    fig, ax = plt.subplots(figsize=(7.0, 4.8))
    for i, (dataset, value, ci, action, n) in enumerate(summary):
        if value is None:
            ax.text(1.02, i, "CPSS pending", va="center", fontsize=8, color=COLORS["pending"])
            continue
        lower = value - ci[0] if ci else 0
        upper = ci[1] - value if ci else 0
        marker = "o" if action == "compress" else "D"
        ax.errorbar(value, i, xerr=[[lower], [upper]], fmt=marker,
                    color=COLORS[action], ecolor=COLORS[action], capsize=3,
                    markersize=5, linewidth=1.1, label=action if i == 0 else None)
        ax.text(value + max(0.035, (ax.get_xlim()[1] - ax.get_xlim()[0]) * 0.006), i,
                f"{value:.2f}×", va="center", fontsize=8)

    ax.axvline(1.0, color="#444444", linestyle="--", linewidth=0.9)
    ax.text(1.0, len(summary) - 0.25, "Raw = 1", ha="center", va="bottom", fontsize=8, color="#444444")
    ax.set_yticks(y)
    ax.set_yticklabels([DATASET_LABELS[d] for d, *_ in summary])
    ax.invert_yaxis()
    ax.set_xlim(0.85, 2.85)
    ax.set_xlabel("Speedup over Raw: T_ready(Raw) / T_ready(CPSS)")
    ax.set_title("CPSS versus Raw (provisional; NOAA pending)")
    ax.grid(axis="x", color="#DDDDDD", linewidth=0.7)
    handles = [
        plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["compress"],
                   markeredgecolor=COLORS["compress"], label="CPSS selects compression", markersize=6),
        plt.Line2D([0], [0], marker="D", color="none", markerfacecolor=COLORS["raw"],
                   markeredgecolor=COLORS["raw"], label="CPSS selects Raw", markersize=6),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8)
    fig.text(0.01, 0.01, "Intervals: paired-repeat bootstrap over completed rows; Hurricane uses pre-repair CPSS behavior.", fontsize=7, color="#555555")
    save(fig, output, "fig2_cpss_vs_raw_provisional")


def figure_decision_overhead(rows: list[dict[str, str]], output: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    label_offsets = {
        "cesm_atm": (6, 12),
        "hurricane_isabel": (6, 10),
        "miranda": (6, -34),
        "nyx": (6, 24),
        "hacc": (6, -50),
        "scale_letkf": (6, 42),
        "exafel": (6, 5),
        "rcsb_mmcif": (6, 4),
        "ncbi_grch38_fasta": (6, 14),
    }
    for dataset in DATASET_ORDER:
        data = [r for r in completed(rows, "cpss-adaptive") if r.get("dataset") == dataset]
        probe = [number(r, "probe_time_s") for r in data]
        speed = paired_speedups(rows, dataset)
        probe = [v for v in probe if v is not None and v > 0]
        if not probe or not speed:
            continue
        x = statistics.median(probe)
        y = statistics.median(speed)
        action_counts = Counter(r.get("selected_action") for r in data)
        action = action_counts.most_common(1)[0][0]
        ax.scatter(x, y, s=50, marker="o" if action == "compress" else "D",
                   color=COLORS[action], edgecolor="white", linewidth=0.7, zorder=3)
        ax.annotate(DATASET_LABELS[dataset], (x, y),
                    xytext=label_offsets.get(dataset, (6, 5)),
                    textcoords="offset points", fontsize=8)

    ax.axhline(1.0, color="#444444", linestyle="--", linewidth=0.9)
    ax.set_xscale("log")
    ax.set_xlabel("Median CPSS probe time (s, log scale)")
    ax.set_ylabel("Speedup over Raw")
    ax.set_title("Probe cost and end-to-end decision outcome")
    ax.grid(color="#DDDDDD", linewidth=0.7)
    handles = [
        plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["compress"],
                   markeredgecolor=COLORS["compress"], label="Compression selected", markersize=6),
        plt.Line2D([0], [0], marker="D", color="none", markerfacecolor=COLORS["raw"],
                   markeredgecolor=COLORS["raw"], label="Raw selected", markersize=6),
    ]
    ax.legend(handles=handles, frameon=False, fontsize=8, loc="best")
    fig.text(0.01, 0.01, "Only datasets with completed CPSS and paired Raw rows are shown; NOAA is pending.", fontsize=7, color="#555555")
    save(fig, output, "fig3_probe_cost_vs_speedup_provisional")


def figure_time_breakdown(rows: list[dict[str, str]], output: Path) -> None:
    selected = ["cesm_atm", "exafel", "miranda", "rcsb_mmcif"]
    components = ["probe", "encode", "transfer", "restore"]
    labels = {"probe": "scan + probe", "encode": "encode", "transfer": "GridFlux", "restore": "decode + rebuild"}
    values: dict[str, dict[str, list[float]]] = {}
    raw_transfer: dict[str, float] = {}
    for dataset in selected:
        cp = [r for r in completed(rows, "cpss-adaptive") if r.get("dataset") == dataset]
        if not cp:
            continue
        values[dataset] = {}
        values[dataset]["probe"] = [
            (number(r, "scan_time_s") or 0.0) + (number(r, "probe_time_s") or 0.0)
            for r in cp
        ]
        values[dataset]["encode"] = [(number(r, "compress_time_s") or 0.0) for r in cp]
        values[dataset]["transfer"] = [(number(r, "gridflux_transfer_time_s") or 0.0) for r in cp]
        values[dataset]["restore"] = [
            (number(r, "remote_decompress_time_s") or 0.0) + (number(r, "remote_rebuild_time_s") or 0.0)
            for r in cp
        ]
        raw = [r for r in completed(rows, "raw") if r.get("dataset") == dataset]
        raw_transfer[dataset] = statistics.median([(number(r, "T_ready_s") or 0.0) for r in raw])

    fig, ax = plt.subplots(figsize=(7.2, 4.7))
    x = np.arange(len(values))
    width = 0.36
    for idx, dataset in enumerate(values):
        bottom = 0.0
        for component in components:
            height = statistics.median(values[dataset][component])
            ax.bar(idx - width / 2, height, width, bottom=bottom, color=COLORS[component],
                   label=labels[component] if idx == 0 else None)
            bottom += height
        ax.bar(idx + width / 2, raw_transfer[dataset], width, color="#BDBDBD",
               label="Raw T_ready" if idx == 0 else None)
    ax.set_xticks(x)
    ax.set_xticklabels([DATASET_LABELS[d] for d in values])
    ax.set_ylabel("Median time (s)")
    ax.set_title("Where CPSS spends time (provisional)")
    ax.grid(axis="y", color="#DDDDDD", linewidth=0.7)
    ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper left")
    fig.text(0.01, 0.01, "CPSS bars use median component times; Raw bars use median T_ready. NOAA and Hurricane repair are not shown.", fontsize=7, color="#555555")
    save(fig, output, "fig4_time_breakdown_provisional")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = load_rows(args.csv)
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.titlesize": 11,
        "axes.labelsize": 9,
        "figure.dpi": 120,
        "savefig.facecolor": "white",
    })
    figure_architecture(args.output)
    figure_cpss_vs_raw(rows, args.output)
    figure_decision_overhead(rows, args.output)
    figure_time_breakdown(rows, args.output)


if __name__ == "__main__":
    main()

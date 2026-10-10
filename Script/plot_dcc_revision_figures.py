"""Build the consolidated color figure for the 2026-10-04 DCC revision."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def row_for(rows: list[dict[str, str]], group: str, scope: str, method: str) -> dict[str, str]:
    return next(row for row in rows
                if row["experiment_group"] == group and row["scope"] == scope
                and row["method"] == method)


METHOD_COLORS = {"raw": "#667085", "zstd-3": "#2F80C0", "cpss-adaptive": "#009E73"}


def color_style() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIX Two Text", "STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.linewidth": 0.65,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })


def setup_axis(ax: plt.Axes, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="center", fontweight="bold", pad=6)
    ax.set_ylabel(ylabel)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#555555")
    ax.spines[["left", "bottom"]].set_linewidth(0.65)
    ax.grid(axis="y", color="#D8D8D8", linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(length=2.5, width=0.55, color="#555555")


def add_bars(ax: plt.Axes, centers: np.ndarray, values: list[float], width: float,
             labels: list[str], offsets: list[float] | None = None) -> None:
    fills = [METHOD_COLORS["raw"], METHOD_COLORS["zstd-3"], METHOD_COLORS["cpss-adaptive"]]
    hatches = ["", "///", "xx"]
    for index, (value, label) in enumerate(zip(values, labels)):
        offset = offsets[index] if offsets else 0.0
        bars = ax.bar(centers + offset, value, width,
                      color=fills[index % 3], edgecolor="#252525", linewidth=0.65,
                      hatch=hatches[index % 3], label=label, zorder=3)
        for bar in bars:
            ax.annotate(f"{bar.get_height():.1f}",
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 2), textcoords="offset points", ha="center",
                        va="bottom", fontsize=6.4)


def make_figure(data_root: Path, output: Path) -> None:
    aggregates = read_csv(data_root / "analysis" / "method_aggregate_summary.csv")
    datasets = read_csv(data_root / "analysis" / "dataset_method_summary.csv")
    bandwidth = read_csv(data_root / "analysis" / "assumed_bandwidth_summary.csv")
    sampling = read_csv(data_root / "analysis" / "sampling_budget_summary.csv")
    color_style()

    fig, axes = plt.subplots(2, 2, figsize=(6.45, 5.0))
    fig.subplots_adjust(left=0.105, right=0.985, top=0.88, bottom=0.20,
                        wspace=0.26, hspace=0.34)
    ax = axes[0, 0]
    setup_axis(ax, "(a) Common-codec pool", "Median-sum $T_{\\mathrm{ready}}$ (s)")
    methods = ["raw", "zstd-3", "cpss-adaptive"]
    values = [float(row_for(aggregates, "codec_pool_20261002", "all", method)["median_sum_T_ready_s"])
              for method in methods]
    bars = ax.bar(np.arange(3), values, width=0.62,
                  color=[METHOD_COLORS["raw"], METHOD_COLORS["zstd-3"], METHOD_COLORS["cpss-adaptive"]], edgecolor="#25364A",
                  linewidth=0.65, hatch=["", "///", "xx"], zorder=3)
    ax.set_xticks(np.arange(3), ["Raw", "Zstd-3", "CPSS"])
    ax.set_ylim(0, max(values) * 1.22)
    for bar in bars:
        ax.annotate(f"{bar.get_height():.1f}",
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    xytext=(0, 2), textcoords="offset points", ha="center",
                    va="bottom", fontsize=6.5)
    ax.text(0.98, 0.95, "3 workloads; 3 runs each\nCPSS: Raw 5/9, Zstd 4/9",
            transform=ax.transAxes, ha="right", va="top", fontsize=6.7,
            bbox={"boxstyle": "square,pad=0.25", "fc": "white", "ec": "#888888", "lw": 0.5})

    ax = axes[0, 1]
    setup_axis(ax, "(b) Raw fallback on HACC and NYX", "$T_{\\mathrm{ready}}$ (s)")
    methods = ["raw", "zstd-3", "cpss-adaptive"]
    workloads = ["hacc", "nyx"]
    centers = np.arange(len(workloads), dtype=float)
    width = 0.22
    for i, method in enumerate(methods):
        values = [float(next(row for row in datasets
                             if row["experiment_group"] == "raw_fallback_20261003"
                             and row["dataset"] == dataset and row["method"] == method)["median_T_ready_s"])
                  for dataset in workloads]
        ax.bar(centers + (i - 1) * width, values, width,
                      color=[METHOD_COLORS["raw"], METHOD_COLORS["zstd-3"], METHOD_COLORS["cpss-adaptive"]][i],
                      edgecolor="#252525", linewidth=0.6,
                      hatch=["", "///", "xx"][i], zorder=3)
    ax.set_xticks(centers, ["HACC", "NYX"])
    ax.set_ylim(0, 790)
    for x, workload in zip(centers, workloads):
        values = [float(next(row for row in datasets
                             if row["experiment_group"] == "raw_fallback_20261003"
                             and row["dataset"] == workload and row["method"] == method)["median_T_ready_s"])
                  for method in methods]
        ax.text(x, max(values) + 17, " / ".join(f"{value:.0f}" for value in values),
                ha="center", va="bottom", fontsize=5.9)

    ax = axes[1, 0]
    setup_axis(ax, "(c) Assumed-bandwidth sensitivity", "Median-sum $T_{\\mathrm{ready}}$ (s)")
    centers = np.arange(2, dtype=float)
    methods = ["raw", "zstd-3", "cpss-adaptive"]
    width = 0.22
    for i, method in enumerate(methods):
        values = [float(row_for(bandwidth, "bandwidth_sensitivity_20261003", rate, method)["median_sum_T_ready_s"])
                  for rate in ["10.0", "100.0"]]
        bars = ax.bar(centers + (i - 1) * width, values, width,
                      color=[METHOD_COLORS["raw"], METHOD_COLORS["zstd-3"], METHOD_COLORS["cpss-adaptive"]][i],
                      edgecolor="#252525", linewidth=0.6,
                      hatch=["", "///", "xx"][i], zorder=3)
        for bar in bars:
            ax.annotate(f"{bar.get_height():.1f}",
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 2), textcoords="offset points", ha="center",
                        va="bottom", fontsize=5.8, rotation=90)
    ax.set_xticks(centers, ["10 Mbps assumption\nCPSS: compress 9/9",
                            "100 Mbps assumption\nCPSS: compress 3/9, Raw 6/9"])
    ax.set_ylim(0, 58)

    ax = axes[1, 1]
    setup_axis(ax, "(d) Probe cost and paired-reference excess", "Median excess $T_{\\mathrm{ready}}$ (%)")
    records = {int(row["sampling_budget"]): row for row in sampling
               if row["dataset"] == "ALL"}
    budgets = [1, 2, 5]
    regret = [float(records[b]["median_regret_vs_paired_measured_raw_zstd_pct"])
              for b in budgets]
    actual_mib = [float(records[b]["median_sampled_MiB"]) for b in budgets]
    probe_s = [float(records[b]["median_probe_s"]) for b in budgets]
    agreement = [float(records[b]["action_agreement_pct"]) for b in budgets]
    bars = ax.bar(np.arange(3), regret, width=0.58,
                  color="#F2B544", edgecolor="#A86600", linewidth=0.65,
                  hatch=["", "///", "xx"], zorder=3)
    ax.set_xticks(np.arange(3), [f"cap {b} window\nactual {actual_mib[i]:.0f} MiB · {probe_s[i]:.2f} s"
                                for i, b in enumerate(budgets)])
    ax.set_ylim(0, 22)
    for i, bar in enumerate(bars):
        ax.annotate(f"{regret[i]:.1f}%",
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    xytext=(0, 2), textcoords="offset points", ha="center",
                    va="bottom", fontsize=6.5)
    ax.text(0.98, 0.95, f"Action agreement: {agreement[0]:.1f}% (9 pairs per cap)",
            transform=ax.transAxes, ha="right", va="top", fontsize=6.2)
    ax.set_xlabel("Probe-window cap (4 MiB per window)")

    # A compact marker key at the top keeps the plot area clean and avoids a
    # second, hatch-heavy legend competing with the panel captions.
    legend_handles = [
        Line2D([0], [0], marker="o", linestyle="None", markersize=6.0,
               markerfacecolor=METHOD_COLORS["raw"], markeredgecolor="#25364A", label="Raw"),
        Line2D([0], [0], marker="o", linestyle="None", markersize=6.0,
               markerfacecolor=METHOD_COLORS["zstd-3"], markeredgecolor="#25364A", label="Zstd-3"),
        Line2D([0], [0], marker="o", linestyle="None", markersize=6.0,
               markerfacecolor=METHOD_COLORS["cpss-adaptive"], markeredgecolor="#25364A", label="CPSS"),
    ]
    fig.legend(handles=legend_handles, loc="upper center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, 0.975), handlelength=0.8, columnspacing=1.8,
               handletextpad=0.45, fontsize=7.2)
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / "fig4_experimental_analysis.pdf", bbox_inches="tight",
                metadata={"Title": "Additional system evidence"})
    fig.savefig(output / "fig4_experimental_analysis.png", dpi=450, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    make_figure(args.data_root.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

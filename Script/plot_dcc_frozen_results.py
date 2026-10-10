"""Generate compact, publication-ready DCC result figures from the frozen analysis.

Run from the repository root:
    py Script/plot_dcc_frozen_results.py

The source CSVs are read-only. Vector PDF figures are written to the manuscript's
Figures directory; PNG companions are previews only.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


DATASETS = [
    ("cesm_atm", "CESM", "science"),
    ("hurricane_isabel", "Hurricane", "science"),
    ("miranda", "Miranda", "science"),
    ("nyx", "NYX", "science"),
    ("hacc", "HACC", "science"),
    ("scale_letkf", "SCALE", "science"),
    ("exafel", "ExaFEL", "science"),
    ("rcsb_mmcif", "RCSB", "text"),
    ("ncbi_grch38_fasta", "NCBI", "text"),
    ("noaa_gsod_csv", "NOAA", "text"),
]

COLORS = {
    "compress": "#0072B2",
    "raw": "#D55E00",
    "zstd": "#6B7280",
    "probe": "#56B4E9",
    "grid": "#D9DEE3",
    "text": "#4B5563",
    "science_band": "#F6F9FB",
    "text_band": "#FBF8F5",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str) -> float:
    value = float(row[key])
    if not math.isfinite(value):
        raise ValueError(f"Non-finite {key} in row: {row}")
    return value


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["STIX Two Text", "STIXGeneral", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "font.size": 7.4,
            "axes.labelsize": 7.4,
            "axes.titlesize": 8.0,
            "xtick.labelsize": 6.8,
            "ytick.labelsize": 7.0,
            "legend.fontsize": 6.8,
            "axes.linewidth": 0.65,
            "lines.linewidth": 1.15,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_dir / f"{stem}.pdf",
        bbox_inches="tight",
        metadata={"Title": stem.replace("_", " ")},
    )
    fig.savefig(output_dir / f"{stem}.png", dpi=400, bbox_inches="tight")
    plt.close(fig)


def row_positions() -> dict[str, float]:
    # Leave a visible gap between scientific/HPC workloads and scientific text.
    return {
        "cesm_atm": 9.5,
        "hurricane_isabel": 8.5,
        "miranda": 7.5,
        "nyx": 6.5,
        "hacc": 5.5,
        "scale_letkf": 4.5,
        "exafel": 3.5,
        "rcsb_mmcif": 1.8,
        "ncbi_grch38_fasta": 0.8,
        "noaa_gsod_csv": -0.2,
    }


def workload_figure(analysis_dir: Path, output_dir: Path) -> None:
    pairwise = {
        row["dataset"]: row
        for row in read_csv(analysis_dir / "dataset_pairwise_summary.csv")
    }
    # Probe time is recorded only on the CPSS path; other method rows contain
    # zeros (or NaN for Raw), so do not let later rows overwrite CPSS values.
    dataset_summary = {
        row["dataset"]: row
        for row in read_csv(analysis_dir / "main_dataset_method_summary.csv")
        if row["method"] == "cpss-adaptive"
    }
    positions = row_positions()
    fig = plt.figure(figsize=(6.35, 3.0))
    grid = fig.add_gridspec(
        1,
        2,
        width_ratios=(3.65, 1.25),
        left=0.19,
        right=0.985,
        top=0.91,
        bottom=0.21,
        wspace=0.26,
    )
    ax = fig.add_subplot(grid[0, 0])
    ax_probe = fig.add_subplot(grid[0, 1], sharey=ax)

    science_y = [positions[key] for key, _, kind in DATASETS if kind == "science"]
    text_y = [positions[key] for key, _, kind in DATASETS if kind == "text"]
    ax.axhspan(min(science_y) - 0.45, max(science_y) + 0.45, color=COLORS["science_band"], zorder=0)
    ax.axhspan(min(text_y) - 0.45, max(text_y) + 0.45, color=COLORS["text_band"], zorder=0)
    ax_probe.axhspan(min(science_y) - 0.45, max(science_y) + 0.45, color=COLORS["science_band"], zorder=0)
    ax_probe.axhspan(min(text_y) - 0.45, max(text_y) + 0.45, color=COLORS["text_band"], zorder=0)
    separator_y = (max(text_y) + min(science_y)) / 2
    for axis in (ax, ax_probe):
        axis.axhline(separator_y, color="#AEB6BD", lw=0.6, ls=(0, (2, 2)), zorder=1)
        axis.set_ylim(-0.7, 10.1)
        axis.spines[["top", "right"]].set_visible(False)
        axis.spines["left"].set_visible(False)
        axis.tick_params(axis="y", length=0, pad=4)
    ax_probe.spines["left"].set_visible(False)

    labels = {key: label for key, label, _ in DATASETS}
    ax.set_yticks([positions[key] for key, _, _ in DATASETS])
    ax.set_yticklabels([labels[key] for key, _, _ in DATASETS])
    ax_probe.tick_params(axis="y", labelleft=False)
    ax.set_xlim(-30, 90)
    ax.set_xticks([-20, 0, 20, 40, 60, 80])
    ax.grid(axis="x", color=COLORS["grid"], lw=0.55, zorder=0)
    ax.axvline(0, color="#343A40", lw=0.85, ls=(0, (3, 2)), zorder=2)
    ax.set_axisbelow(True)
    ax.set_xlabel("Reduction in $T_{\\mathrm{ready}}$ vs. Raw (%)")
    ax.set_title("(a) Workload-level end-to-end outcome", loc="left", pad=6)

    ax_probe.set_xlim(0, 17.5)
    ax_probe.set_xticks([0, 5, 10, 15])
    ax_probe.grid(axis="x", color=COLORS["grid"], lw=0.55, zorder=0)
    ax_probe.set_axisbelow(True)
    ax_probe.set_xlabel("Probe / $T_{\\mathrm{ready}}$ (%)")
    ax_probe.set_title("(b) Probe share", loc="left", pad=6)

    for dataset, _, _ in DATASETS:
        row = pairwise[dataset]
        summary = dataset_summary[dataset]
        y = positions[dataset]
        raw_median = number(row, "raw_T_ready_median_s")
        cp_reduction = number(row, "cpss_vs_raw_time_reduction_pct")
        zstd_reduction = 100 * (
            raw_median - number(row, "zstd_T_ready_median_s")
        ) / raw_median
        compression_runs = int(number(row, "compress_repeats"))
        raw_runs = int(number(row, "raw_repeats"))
        selected_color = COLORS["compress"] if compression_runs >= raw_runs else COLORS["raw"]
        selected_marker = "o" if compression_runs >= raw_runs else "D"

        ax.plot(
            [zstd_reduction, cp_reduction],
            [y - 0.13, y + 0.13],
            color="#ADB5BD",
            lw=0.8,
            zorder=2,
        )
        ax.scatter(
            [zstd_reduction],
            [y - 0.13],
            marker="s",
            s=21,
            color=COLORS["zstd"],
            edgecolor="white",
            linewidth=0.45,
            zorder=3,
        )
        ax.scatter(
            [cp_reduction],
            [y + 0.13],
            marker=selected_marker,
            s=25,
            color=selected_color,
            edgecolor="white",
            linewidth=0.5,
            zorder=4,
        )
        cp_time = number(row, "cpss_T_ready_median_s")
        probe_median = number(summary, "cpus_probe_median_s")
        probe_share = 100 * probe_median / cp_time
        ax_probe.barh(
            y,
            probe_share,
            height=0.42,
            color=COLORS["probe"],
            edgecolor="white",
            linewidth=0.35,
            zorder=2,
        )
        ax_probe.text(
            min(probe_share + 0.35, 16.95),
            y,
            f"{probe_share:.1f}",
            ha="left",
            va="center",
            fontsize=6.1,
            color=COLORS["text"],
        )

    handles = [
        Line2D([0], [0], marker="s", color="none", markerfacecolor=COLORS["zstd"],
               markeredgecolor=COLORS["zstd"], markersize=4.2, label="Fixed Zstd-3"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=COLORS["compress"],
               markeredgecolor=COLORS["compress"], markersize=4.2, label="CPSS selects compression"),
        Line2D([0], [0], marker="D", color="none", markerfacecolor=COLORS["raw"],
               markeredgecolor=COLORS["raw"], markersize=4.0, label="CPSS selects Raw"),
    ]
    fig.legend(
        handles=handles,
        loc="lower center",
        bbox_to_anchor=(0.54, 0.025),
        ncol=3,
        frameon=False,
        handletextpad=0.4,
        columnspacing=1.05,
    )
    save_figure(fig, output_dir, "fig3_workload_outcomes")


def bandwidth_figure(analysis_dir: Path, output_dir: Path) -> None:
    summary = read_csv(analysis_dir / "bandwidth_sensitivity_summary.csv")
    model: dict[str, dict[int, float]] = {}
    for row in summary:
        method = row["method"]
        if method not in {"raw", "zstd-3", "cpss-adaptive"}:
            continue
        model.setdefault(method, {})[int(number(row, "bandwidth_mbps"))] = number(
            row, "total_time_speedup"
        )

    measured = {
        row["method"]: number(row, "speedup_vs_raw_median_sum")
        for row in read_csv(analysis_dir / "main_method_summary.csv")
        if row["method"] in {"raw", "zstd-3", "cpss-adaptive"}
    }
    rates = [10, 100, 1000]
    fig, ax = plt.subplots(figsize=(6.35, 2.3))
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xscale("log")
    ax.set_xticks(rates, labels=["10", "100", "1,000"])
    ax.set_xlim(8, 1300)
    ax.set_ylim(0.15, 1.47)
    ax.set_yticks([0.25, 0.5, 0.75, 1.0, 1.25])
    ax.grid(axis="both", color=COLORS["grid"], lw=0.55)
    ax.set_axisbelow(True)
    ax.axhline(1.0, color="#343A40", lw=0.85, ls=(0, (3, 2)), zorder=1)
    ax.axvline(100, color="#8A9299", lw=0.7, ls=(0, (2, 2)), zorder=1)

    series = [
        ("raw", "Raw", "#555B61", "--", "o"),
        ("zstd-3", "Fixed Zstd-3", "#E69F00", "-", "s"),
        ("cpss-adaptive", "CPSS (frozen policy)", "#0072B2", "-", "o"),
    ]
    for key, label, color, linestyle, marker in series:
        values = [model[key][rate] for rate in rates]
        ax.plot(
            rates,
            values,
            color=color,
            linestyle=linestyle,
            marker=marker,
            markersize=4.0,
            markeredgewidth=0.7,
            label=label,
            zorder=3,
        )
        if key != "raw":
            ax.scatter(
                [100],
                [measured[key]],
                marker="o",
                s=42,
                facecolor="white",
                edgecolor=color,
                linewidth=1.05,
                zorder=5,
            )

    ax.set_xlabel("Configured bandwidth (Mbps, log scale)")
    ax.set_ylabel("Speedup over Raw ($T_{\\mathrm{ready}}$)")
    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.18),
        ncol=3,
        frameon=False,
        handlelength=1.7,
        columnspacing=1.4,
    )
    save_figure(fig, output_dir, "fig4_bandwidth_sensitivity")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-dir",
        type=Path,
        default=root / "ExperimentRecords" / "dcc_analysis_20260930" / "analysis",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "docs" / "dcc_latex" / "cpss_submission" / "Figures",
    )
    args = parser.parse_args()
    configure_style()
    workload_figure(args.analysis_dir, args.output_dir)
    bandwidth_figure(args.analysis_dir, args.output_dir)
    print(f"Wrote 2 PDF figures and PNG previews to {args.output_dir}")


if __name__ == "__main__":
    main()

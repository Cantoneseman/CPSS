"""Create the two compact experimental figures for the DCC manuscript.

The first figure uses the frozen main-analysis CSVs to explain the overall
result, timing composition, workload dependence, and probe cost.  The second
uses the measured 10/100 Mbps sensitivity CSV to compare the reduced-suite
results and action-cost composition.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch


COLORS = {
    "cpss": "#0072B2",
    "raw": "#4D5156",
    "zstd": "#6B7280",
    "lz4": "#E69F00",
    "gzip": "#009E73",
    "xz": "#CC79A7",
    "probe": "#56B4E9",
    "wire": "#F0A202",
    "encode": "#0072B2",
    "decode": "#8DA0CB",
    "rebuild": "#A6D854",
    "grid": "#D9DEE3",
    "text": "#4B5563",
}
METHOD_COLORS = {
    "raw": "#667085",
    "zstd-3": "#2F80C0",
    "cpss-adaptive": "#009E73",
    "lz4": "#E69F00",
    "gzip-6": "#56B4E9",
    "xz-6": "#CC79A7",
}

METHOD_LABELS = {
    "raw": "Raw",
    "zstd-3": "Fixed Zstd-3",
    "cpss-adaptive": "CPSS",
    "lz4": "LZ4",
    "gzip-6": "gzip-6",
    "xz-6": "XZ-6",
}
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def finite(value: str | None) -> float:
    if value is None or value == "" or value.lower() == "nan":
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["STIX Two Text", "STIXGeneral", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "font.size": 8.6,
            "axes.labelsize": 8.6,
            "axes.titlesize": 9.2,
            "xtick.labelsize": 7.8,
            "ytick.labelsize": 7.8,
            "legend.fontsize": 7.4,
            "axes.linewidth": 0.65,
            "lines.linewidth": 1.2,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "savefig.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def save(fig: plt.Figure, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.pdf", bbox_inches="tight", metadata={"Title": name})
    fig.savefig(out / f"{name}.png", dpi=400, bbox_inches="tight")
    plt.close(fig)


def positions() -> dict[str, float]:
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


def figure_end_to_end(analysis: Path, out: Path) -> None:
    method_rows = read_csv(analysis / "main_method_summary.csv")
    method = {row["method"]: row for row in method_rows}
    pairwise = {row["dataset"]: row for row in read_csv(analysis / "dataset_pairwise_summary.csv")}
    ds_rows = [
        row for row in read_csv(analysis / "main_dataset_method_summary.csv")
        if row["method"] in METHOD_LABELS
    ]
    ds_by_method = {(row["dataset"], row["method"]): row for row in ds_rows}

    method_order = ["cpss-adaptive", "zstd-3", "raw", "lz4", "gzip-6", "xz-6"]
    stage_fields = {
        "Probe": "cpus_probe_median_s",
        "Encode": "compress_median_s",
        "Wire": "gridflux_transfer_median_s",
        "Decode": "remote_decompress_median_s",
        "Rebuild": "remote_rebuild_median_s",
    }
    stage_colors = {
        "Probe": COLORS["probe"],
        "Encode": COLORS["encode"],
        "Wire": COLORS["wire"],
        "Decode": COLORS["decode"],
        "Rebuild": COLORS["rebuild"],
    }
    stage_hatches = {
        "Probe": "..",
        "Encode": "//",
        "Wire": "",
        "Decode": "xx",
        "Rebuild": "\\\\",
    }
    stage_totals: dict[str, dict[str, float]] = {}
    for m in method_order:
        rows = [r for r in ds_rows if r["method"] == m]
        stage_totals[m] = {
            name: sum(finite(r.get(field)) for r in rows)
            for name, field in stage_fields.items()
        }

    pos = positions()
    # Use thin stems and points rather than filled bars.  The point locations
    # carry the comparison; large rectangular fills only obscure the small
    # differences between methods when the figure is reduced in the paper.
    fig = plt.figure(figsize=(6.25, 4.42))
    gs = fig.add_gridspec(
        2, 2, width_ratios=(1.13, 0.87), height_ratios=(0.92, 1.35),
        left=0.16, right=0.985, top=0.94, bottom=0.18,
        wspace=0.46, hspace=0.72,
    )

    # (a) Overall method comparison.
    ax = fig.add_subplot(gs[0, 0])
    speedups = [finite(method[m]["speedup_vs_raw_median_sum"]) for m in method_order]
    y = np.arange(len(method_order))
    for yi, value, method_name in zip(y, speedups, method_order):
        is_cpss = method_name == "cpss-adaptive"
        ink = METHOD_COLORS[method_name]
        ax.hlines(yi, 0, value, color=ink, lw=1.7 if is_cpss else 1.0, zorder=2)
        ax.scatter(
            value, yi, s=30 if is_cpss else 26,
            facecolor=METHOD_COLORS[method_name] if is_cpss else "white",
            edgecolor=METHOD_COLORS[method_name], lw=.75, zorder=3,
        )
    ax.axvline(1.0, color="#333333", lw=0.8, ls=(0, (3, 2)), zorder=2)
    ax.set_yticks(y, [METHOD_LABELS[m] for m in method_order])
    ax.invert_yaxis()
    # Keep a full row interval between the last method and the x-axis, as in
    # the workload panel below; Matplotlib's default half-row margin is tight.
    ax.set_ylim(len(method_order), -0.25)
    ax.set_xlim(0, 1.25)
    ax.set_xticks([0.0, 0.5, 1.0])
    ax.grid(axis="x", color="#D0D0D0", lw=0.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Speedup over Raw")
    ax.set_title("(a) Aggregate end-to-end result", loc="center", pad=4)
    for yi, value, method_name in zip(y, speedups, method_order):
        marker_radius = math.sqrt(30 if method_name == "cpss-adaptive" else 26) / 2 + .375
        ax.annotate(f"{value:.3f}", (value, yi), xytext=(marker_radius + 5, 0),
                    textcoords="offset points", va="center", fontsize=7.4,
                    color="#333333", bbox=dict(facecolor="white", edgecolor="none", pad=.1))

    # (b) Total time as a point plot.  The compact text column reports the
    # local-work fraction; the complement is wire time.  This keeps the timing
    # trade-off visible without a full-height 100% stacked rectangle.
    ax = fig.add_subplot(gs[0, 1])
    totals = []
    local_shares = []
    for m in method_order:
        # Keep the plotted total identical to Table 1: sum workload medians
        # of total time, not medians of each stage added together.
        total = finite(method[m]["median_sum_T_ready_s"])
        local = total - stage_totals[m]["Wire"]
        totals.append(total)
        local_shares.append(100 * local / total if total else 0.0)
    for yi, total, method_name in zip(y, totals, method_order):
        is_cpss = method_name == "cpss-adaptive"
        ink = METHOD_COLORS[method_name]
        ax.hlines(yi, 0, total, color=ink, lw=1.7 if is_cpss else 1.0, zorder=2)
        ax.scatter(
            total, yi, s=30 if is_cpss else 26,
            facecolor=METHOD_COLORS[method_name] if is_cpss else "white",
            edgecolor=METHOD_COLORS[method_name], lw=.75, zorder=3,
        )
    ax.set_yticks(y, [METHOD_LABELS[m] for m in method_order])
    ax.invert_yaxis()
    ax.set_ylim(len(method_order), -0.25)
    xmax = max(totals) * 1.22
    ax.set_xlim(0, xmax)
    ax.set_xlabel("Median-sum $T_{\\mathrm{ready}}$ (s)")
    ax.grid(axis="x", color="#D0D0D0", lw=0.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_title("(b) Total time and local share", loc="center", pad=4)
    for yi, total, local_share, method_name in zip(y, totals, local_shares, method_order):
        marker_radius = math.sqrt(30 if method_name == "cpss-adaptive" else 26) / 2 + .375
        ax.annotate(f"{total:.0f}s  |  {local_share:.1f}% local", (total, yi),
                    xytext=(marker_radius + 5, 0), textcoords="offset points", va="center",
                    fontsize=7.0, color="#333333",
                    bbox=dict(facecolor="white", edgecolor="none", pad=.1))

    # (c) Separate method columns avoid occlusion without moving observations
    # off their workload row. Both columns use the same reduction scale.
    lower = gs[1, :].subgridspec(1, 2, width_ratios=(2.12, 1.0), wspace=.42)
    c_pair = lower[0].subgridspec(1, 2, width_ratios=(1, 1), wspace=.07)
    ax_zstd = fig.add_subplot(c_pair[0, 0])
    ax_cpss = fig.add_subplot(c_pair[0, 1], sharey=ax_zstd, sharex=ax_zstd)
    science_y = [pos[key] for key, _, kind in DATASETS if kind == "science"]
    text_y = [pos[key] for key, _, kind in DATASETS if kind == "text"]
    separator = (max(text_y) + min(science_y)) / 2
    for ax, title in ((ax_zstd, "Fixed Zstd-3"), (ax_cpss, "CPSS")):
        ax.axhline(separator, color="#777777", lw=.55, ls=(0, (3, 2)))
        ax.axvline(0, color="#555555", lw=.65)
        ax.set_ylim(-1.2, 10.1)
        ax.set_xlim(-30, 115)
        ax.set_xticks([-20, 0, 40, 80])
        ax.grid(axis="x", color="#E0E0E0", lw=.45)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0, pad=3)
        ax.set_title(title, loc="center", fontsize=8.2, pad=4)
    ax_zstd.set_yticks([pos[key] for key, _, _ in DATASETS], [label for _, label, _ in DATASETS])
    ax_cpss.tick_params(axis="y", labelleft=False)
    c_left = ax_zstd.get_position()
    c_right = ax_cpss.get_position()
    fig.text((c_left.x0 + c_right.x1) / 2, c_left.y1 + .058,
             "(c) Workload dependence", ha="center", fontsize=9.2)
    fig.text((c_left.x0 + c_right.x1) / 2, c_left.y0 - .067,
             "Reduction in $T_{\\mathrm{ready}}$ vs. Raw (%)", ha="center", va="top", fontsize=8.6)
    for dataset, _, _ in DATASETS:
        row = pairwise[dataset]
        raw = finite(row["raw_T_ready_median_s"])
        zstd = 100 * (raw - finite(row["zstd_T_ready_median_s"])) / raw
        cpss = finite(row["cpss_vs_raw_time_reduction_pct"])
        compress_runs = int(float(row["compress_repeats"]))
        raw_runs = int(float(row["raw_repeats"]))
        selects_compression = compress_runs >= raw_runs
        yy = pos[dataset]
        ax_zstd.barh(yy, zstd, height=.22, color=METHOD_COLORS["zstd-3"], zorder=3)
        if selects_compression:
            ax_cpss.barh(yy, cpss, height=.22, color=METHOD_COLORS["cpss-adaptive"], zorder=3)
        else:
            # Raw results are near zero; a diamond makes the bypass action
            # visible even where a proportional bar would be sub-pixel wide.
            ax_cpss.scatter(cpss, yy, marker="D", s=22, facecolor="white",
                            edgecolor=METHOD_COLORS["cpss-adaptive"], lw=.8, zorder=3)
        for ax, value in ((ax_zstd, zstd), (ax_cpss, cpss)):
            if ax is ax_zstd and value < 0:
                # Keep fixed-codec negative labels just to the right of the
                # zero line; placing them at the bar's left endpoint collides
                # with the workload names in this narrow panel.
                anchor, offset, align = 0, (16, 0), "left"
            elif value < 0:
                anchor, offset, align = value, (-5, 0), "right"
            else:
                anchor, offset, align = value, (5, 0), "left"
            ax.annotate(f"{value:.1f}", (anchor, yy),
                        xytext=offset, textcoords="offset points", ha=align,
                        va="center", fontsize=7.3, color="#111111")

    # (d) Probe share, using only CPSS rows.
    ax = fig.add_subplot(lower[1], sharey=ax_zstd)
    ax.axhline((max(text_y) + min(science_y)) / 2, color="#555555", lw=.65, ls=(0, (3, 2)))
    ax.set_ylim(-1.2, 10.1)
    ax.set_xlim(0, 17.5)
    ax.set_xticks([0, 5, 10, 15])
    ax.grid(axis="x", color="#D0D0D0", lw=.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", labelleft=False, length=0)
    ax.set_xlabel("Probe / $T_{\\mathrm{ready}}$ (%)")
    d_position = ax.get_position()
    fig.text((d_position.x0 + d_position.x1) / 2, c_left.y1 + .058,
             "(d) Decision overhead", ha="center", fontsize=9.2)
    for dataset, _, _ in DATASETS:
        row = ds_by_method[(dataset, "cpss-adaptive")]
        share = 100 * finite(row["cpus_probe_median_s"]) / finite(row["T_ready_median_s"])
        yy = pos[dataset]
        ax.hlines(yy, 0, share, color=COLORS["probe"], lw=1.3, zorder=2)
        ax.scatter(share, yy, s=26, color=COLORS["probe"], edgecolor="white", lw=.45,
                   zorder=3, clip_on=False)
        ax.annotate(f"{share:.1f}", (share, yy), xytext=(math.sqrt(26) / 2 + .225 + 5, 0),
                    textcoords="offset points", va="center", fontsize=7.2,
                    color="#111111", bbox=dict(facecolor="white", edgecolor="none", pad=.1))

    legend = [
        Patch(facecolor=METHOD_COLORS["cpss-adaptive"], edgecolor="none", label="CPSS: compress"),
        Line2D([0], [0], marker="D", color="none", markerfacecolor="white", markeredgecolor=METHOD_COLORS["cpss-adaptive"], markersize=3.8, label="CPSS: Raw bypass"),
    ]
    c_center = (c_left.x0 + c_right.x1) / 2
    fig.legend(legend, [item.get_label() for item in legend], loc="lower center",
               bbox_to_anchor=(c_center, .035),
               ncol=2, frameon=False, handletextpad=.35, columnspacing=1.5, fontsize=7.4,
               handlelength=1.25, handleheight=.6)
    save(fig, out, "fig3_end_to_end")


def median(values: list[float]) -> float:
    values = sorted(values)
    n = len(values)
    if n == 0:
        return 0.0
    middle = n // 2
    if n % 2:
        return values[middle]
    return 0.5 * (values[middle - 1] + values[middle])


def figure_bandwidth(csv_path: Path, out: Path) -> None:
    """Plot the two physically measured points in the reduced sensitivity set.

    The CSV contains three workloads and three repetitions per method/rate. We
    first take a per-workload median, then sum those medians, matching the
    aggregate convention used in the main evaluation. No unmeasured rate is
    interpolated or modeled here.
    """
    rows = read_csv(csv_path)
    grouped: dict[tuple[int, str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (int(float(row["bandwidth_mbps"])), row["dataset"], row["method"])
        grouped.setdefault(key, []).append(row)

    rates = sorted({key[0] for key in grouped})
    methods = ["raw", "zstd-3", "cpss-adaptive"]
    datasets = sorted({key[1] for key in grouped})
    speedups: dict[tuple[str, int], float] = {}
    shares: dict[tuple[str, int], tuple[float, float]] = {}
    for rate in rates:
        raw_sum = sum(
            median([finite(row["T_ready_s"]) for row in grouped[(rate, dataset, "raw")]])
            for dataset in datasets
        )
        for method in methods:
            dataset_rows = {
                dataset: grouped[(rate, dataset, method)] for dataset in datasets
            }
            medians = {
                dataset: median([finite(row["T_ready_s"]) for row in rows_for_dataset])
                for dataset, rows_for_dataset in dataset_rows.items()
            }
            total = sum(medians.values())
            speedups[(method, rate)] = raw_sum / total if total else 0.0
            local = sum(
                median([
                    max(0.0, finite(row["T_ready_s"]) - finite(row["gridflux_transfer_time_s"]))
                    for row in rows_for_dataset
                ])
                for rows_for_dataset in dataset_rows.values()
            )
            shares[(method, rate)] = (local / total if total else 0.0, 1.0 - local / total if total else 0.0)

    fig, axes = plt.subplots(1, 2, figsize=(6.25, 2.82), gridspec_kw={"width_ratios": (1.08, 1.0)})
    x = np.arange(len(rates), dtype=float)
    width = .22
    offsets = (np.arange(len(methods)) - (len(methods) - 1) / 2) * width
    hatches = {"raw": "//", "zstd-3": "", "cpss-adaptive": ".."}

    ax = axes[0]
    for offset, method in zip(offsets, methods):
        values = [speedups[(method, rate)] for rate in rates]
        bars = ax.bar(
            x + offset, values, width=width * .88, color=METHOD_COLORS[method],
            edgecolor="#30363B", linewidth=.55, hatch=hatches[method],
            label=METHOD_LABELS[method], zorder=3,
        )
        for bar, value in zip(bars, values):
            ax.annotate(
                f"{value:.2f}", (bar.get_x() + bar.get_width() / 2, value),
                xytext=(0, 3), textcoords="offset points", ha="center", va="bottom",
                fontsize=6.8, color=COLORS["text"],
            )
    ax.axhline(1.0, color="#343A40", lw=.75, ls=(0, (3, 2)), zorder=2)
    ax.set_xticks(x, [f"{rate} Mbps" for rate in rates])
    ax.set_ylim(0, max(1.95, max(speedups.values()) + .27))
    ax.set_yticks([0, .5, 1.0, 1.5])
    ax.grid(axis="y", color=COLORS["grid"], lw=.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_ylabel("Aggregate $T_{\\mathrm{ready}}$ speedup vs. Raw")
    ax.set_title("(a) End-to-end performance", loc="center", pad=4)

    ax = axes[1]
    local_values = {
        method: [100 * shares[(method, rate)][0] for rate in rates]
        for method in methods
    }
    for offset, method in zip(offsets, methods):
        values = local_values[method]
        bars = ax.bar(
            x + offset, values, width=width * .88, color=METHOD_COLORS[method],
            edgecolor="#30363B", linewidth=.55, hatch=hatches[method],
            label=METHOD_LABELS[method], zorder=3,
        )
        for bar, value in zip(bars, values):
            center = bar.get_x() + bar.get_width() / 2
            if value == 0:
                ax.scatter([center], [0], s=9, marker="s", color=METHOD_COLORS[method],
                           edgecolor="#30363B", linewidth=.45, zorder=4, clip_on=False)
                label_y = .18
            else:
                label_y = value + .16
            ax.annotate(
                f"{value:.1f}", (center, label_y), ha="center", va="bottom",
                fontsize=6.8, color=COLORS["text"],
            )
    local_max = max(value for values in local_values.values() for value in values)
    ax.set_xticks(x, [f"{rate} Mbps" for rate in rates])
    ax.set_ylim(0, max(8.6, local_max + 1.0))
    ax.set_yticks([0, 2, 4, 6, 8] if local_max <= 8 else [0, 5, 10, 15])
    ax.set_ylabel("Non-wire share of $T_{\\mathrm{ready}}$ (%)")
    ax.grid(axis="y", color=COLORS["grid"], lw=.5)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("(b) Local work (wire is remainder)", loc="center", pad=4)

    # Use representative bar swatches so the legend mirrors the grouped bars.
    legend_order = ["raw", "zstd-3", "cpss-adaptive"]
    # Use a denser hatch in the compact Raw legend swatch so the diagonal
    # pattern remains clear at paper scale; the plotted bars retain `//`.
    legend_hatches = {**hatches, "raw": "////"}
    handles = [
        Patch(
            facecolor=METHOD_COLORS[method],
            edgecolor="#30363B",
            linewidth=.55,
            hatch=legend_hatches[method],
            label=METHOD_LABELS[method],
        )
        for method in legend_order
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(.52, .005), ncol=3,
               frameon=False, handlelength=1.15, handleheight=1.1, columnspacing=1.0,
               handletextpad=.35, fontsize=7.0)
    fig.subplots_adjust(left=.13, right=.99, top=.87, bottom=.22, wspace=.43)
    save(fig, out, "fig4_bandwidth_sensitivity")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    analysis = root / "ExperimentRecords" / "dcc_analysis_20260930" / "analysis"
    output = root / "docs" / "dcc_latex" / "cpss_submission" / "Figures"
    style()
    figure_end_to_end(analysis, output)
    bandwidth_csv = root / "ExperimentRecords" / "dcc_analysis_20260930" / "ExperimentRecords" / "dcc_bandwidth_sensitivity_measured_10_100_same128" / "method_metrics_10_100mbps.csv"
    figure_bandwidth(bandwidth_csv, output)
    print(f"Wrote two experimental figures to {output}")


if __name__ == "__main__":
    main()

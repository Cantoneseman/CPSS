"""Publication-style DCC figures from method_metrics.csv.

Run after each experiment update, then use the final merged CSV for submission:
python Script/plot_dcc_paper_figures.py --csv <method_metrics.csv> --output figures_final
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
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle
import numpy as np


ORDER = [
    "cesm_atm", "hurricane_isabel", "miranda", "nyx", "hacc",
    "scale_letkf", "exafel", "rcsb_mmcif", "ncbi_grch38_fasta", "noaa_gsod_csv",
]
LABEL = {
    "cesm_atm": "CESM", "hurricane_isabel": "Hurricane", "miranda": "Miranda",
    "nyx": "NYX", "hacc": "HACC", "scale_letkf": "SCALE",
    "exafel": "ExaFEL", "rcsb_mmcif": "RCSB", "ncbi_grch38_fasta": "NCBI",
    "noaa_gsod_csv": "NOAA",
}

# Colorblind-friendly, stable mapping across all figures.
C = {
    "compress": "#0072B2",
    "raw": "#D55E00",
    "probe": "#56B4E9",
    "encode": "#009E73",
    "transfer": "#E69F00",
    "restore": "#CC79A7",
    "other": "#8C8C8C",
    "gray": "#777777",
    "grid": "#D9D9D9",
    "dark": "#252525",
    "light_blue": "#E6F2F8",
    "light_orange": "#FBEEE7",
}

COMPONENTS = ["decision_setup", "encode", "transfer", "restore"]
COMPONENT_LABEL = {
    "decision_setup": "Scan, probe & setup",
    "encode": "Full-data encoding",
    "transfer": "GridFlux transfer",
    "restore": "Decode & rebuild",
}
COMPONENT_COLOR = {
    "decision_setup": C["probe"],
    "encode": C["encode"],
    "transfer": C["transfer"],
    "restore": C["restore"],
}


def load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def val(row: dict[str, str], field: str) -> float:
    try:
        out = float(row.get(field, ""))
        return out if math.isfinite(out) else 0.0
    except (TypeError, ValueError):
        return 0.0


def is_ok(row: dict[str, str]) -> bool:
    return row.get("status") == "completed" and row.get("roundtrip_ok", "True").lower() != "false"


def rows_for(rows: list[dict[str, str]], dataset: str, method: str) -> list[dict[str, str]]:
    return [r for r in rows if r.get("dataset") == dataset and r.get("method") == method and is_ok(r)]


def paired(rows: list[dict[str, str]], dataset: str) -> list[tuple[dict[str, str], dict[str, str]]]:
    index: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for r in rows:
        if r.get("dataset") == dataset and r.get("method") in {"raw", "cpss-adaptive"} and is_ok(r):
            index[r.get("repeat", "")][r["method"]] = r
    return [(v["raw"], v["cpss-adaptive"]) for v in index.values() if {"raw", "cpss-adaptive"} <= v.keys()]


def paired_ratios(rows: list[dict[str, str]], dataset: str) -> list[float]:
    return [val(raw, "T_ready_s") / val(cp, "T_ready_s")
            for raw, cp in paired(rows, dataset)
            if val(raw, "T_ready_s") > 0 and val(cp, "T_ready_s") > 0]


def bootstrap_ci(values: list[float], seed: int) -> tuple[float, float] | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    estimates = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(8000)]
    return float(np.percentile(estimates, 2.5)), float(np.percentile(estimates, 97.5))


def dataset_action(rows: list[dict[str, str]], dataset: str) -> str:
    cp = rows_for(rows, dataset, "cpss-adaptive")
    if not cp:
        return "pending"
    counts = Counter(r.get("selected_action", "") for r in cp)
    return "compress" if counts.get("compress", 0) > counts.get("raw", 0) else "raw"


def style() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIX Two Text", "STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8,
        "axes.titlesize": 9,
        "axes.labelsize": 8,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 8,
        "legend.fontsize": 7,
        "axes.linewidth": 0.65,
        "lines.linewidth": 1.0,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })


def finish(fig: plt.Figure, output: Path, stem: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight", metadata={"Title": stem})
    fig.savefig(output / f"{stem}.png", dpi=450, bbox_inches="tight")
    plt.close(fig)


def arrow(ax: plt.Axes, p1: tuple[float, float], p2: tuple[float, float], *, rad: float = 0) -> None:
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle="-|>", mutation_scale=8, linewidth=0.8,
        color=C["gray"], connectionstyle=f"arc3,rad={rad}", shrinkA=2, shrinkB=2,
    ))


def node(ax: plt.Axes, x: float, y: float, w: float, h: float,
         title: str, subtitle: str = "", edge: str = C["gray"], face: str = "white",
         subtitle_size: float = 6.8) -> None:
    ax.add_patch(Rectangle((x, y), w, h, linewidth=0.85, edgecolor=edge, facecolor=face))
    ax.text(x + w / 2, y + h * 0.59, title, ha="center", va="center", fontsize=8.2, color=C["dark"])
    if subtitle:
        ax.text(x + w / 2, y + h * 0.27, subtitle, ha="center", va="center", fontsize=subtitle_size, color=C["gray"])


def fig_architecture(output: Path) -> None:
    """Explain CPSS's estimate/decision stage and the two end-to-end paths."""
    fig, ax = plt.subplots(figsize=(6.35, 3.05))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    # Two aligned swimlanes make the estimate/control path distinct from the
    # bytes that actually travel across the GridFlux link.
    ax.add_patch(Rectangle((0.008, 0.575), 0.686, 0.33, facecolor="#F4F8FA", edgecolor="none", zorder=0))
    ax.add_patch(Rectangle((0.008, 0.105), 0.686, 0.39, facecolor="#FBFBFA", edgecolor="none", zorder=0))
    ax.text(0.015, 0.925, "SOURCE DOMAIN  /  DECISION PLANE", ha="left", va="center",
            fontsize=7.2, fontweight="bold", color=C["dark"])
    ax.text(0.690, 0.525, "SOURCE DOMAIN  /  DATA PLANE", ha="right", va="center",
            fontsize=7.2, fontweight="bold", color=C["dark"])
    ax.text(0.86, 0.925, "DESTINATION DOMAIN", ha="center", va="center",
            fontsize=7.2, fontweight="bold", color=C["dark"])
    ax.plot([0.715, 0.715], [0.11, 0.905], color="#A6A6A6", lw=0.8, ls=(0, (3, 2)))

    # Calibration/decision stages. The estimate extrapolates sampled pipeline
    # cost to the dataset and compares it with Raw under the configured guard.
    stages = [
        (0.025, 0.128, "Scan + map", "files · 128 MiB blocks", C["probe"]),
        (0.185, 0.132, "Random sample", "n blocks · fixed seed", C["probe"]),
        (0.350, 0.145, "Profile candidates", "encode · decode · bytes", C["probe"]),
        (0.530, 0.145, "Cost model + guard", "$T_c + B_c/BW + T_d$\nvs. Raw transfer", C["compress"]),
    ]
    for number, (x, w, title, subtitle, edge) in enumerate(stages, start=1):
        node(ax, x, 0.665, w, 0.155, title, subtitle, edge=edge,
             face="#FFFFFF", subtitle_size=6.4)
        ax.add_patch(plt.Circle((x + 0.012, 0.842), 0.014, facecolor=edge,
                                edgecolor="white", linewidth=0.45, zorder=4))
        ax.text(x + 0.012, 0.842, str(number), ha="center", va="center",
                fontsize=5.2, color="white", fontweight="bold", zorder=5)
    for start, end in [((0.153, 0.742), (0.185, 0.742)),
                       ((0.317, 0.742), (0.350, 0.742)),
                       ((0.495, 0.742), (0.530, 0.742))]:
        arrow(ax, start, end)

    # Dataset bytes flow through the selected action, converge at GridFlux,
    # then the receiver either restores a bundle or accepts the Raw tree.
    node(ax, 0.025, 0.235, 0.105, 0.17, "Dataset", "directory tree", face="white")
    selector = Polygon([[0.204, 0.426], [0.265, 0.337], [0.204, 0.248], [0.143, 0.337]],
                       closed=True, edgecolor=C["gray"], facecolor="white", linewidth=0.9)
    ax.add_patch(selector)
    ax.text(0.204, 0.337, "Select\npath", ha="center", va="center", fontsize=6.6,
            color=C["dark"], fontweight="bold")
    node(ax, 0.300, 0.325, 0.165, 0.145, "Encode + bundle", "selected pipeline · all blocks",
         edge=C["compress"], face=C["light_blue"], subtitle_size=6.2)
    node(ax, 0.300, 0.135, 0.165, 0.145, "Raw bypass", "send original tree", edge=C["raw"],
         face=C["light_orange"], subtitle_size=6.2)
    node(ax, 0.515, 0.235, 0.145, 0.19, "GridFlux", "cross-domain · CRC32C\n100 Mbps · 2 connections",
         edge=C["transfer"], face="#FFF9EB", subtitle_size=6.2)
    node(ax, 0.750, 0.235, 0.225, 0.19, "Rebuild usable tree", "decode bundle  |  pass Raw",
         edge=C["restore"], face="#FBF4F8", subtitle_size=6.2)

    arrow(ax, (0.130, 0.320), (0.143, 0.337))
    arrow(ax, (0.265, 0.362), (0.300, 0.397), rad=-0.03)
    arrow(ax, (0.265, 0.312), (0.300, 0.207), rad=0.03)
    arrow(ax, (0.465, 0.397), (0.515, 0.365), rad=0.02)
    arrow(ax, (0.465, 0.207), (0.515, 0.295), rad=-0.02)
    arrow(ax, (0.660, 0.330), (0.750, 0.330))

    # Dashed control signal from estimated action to the data-path selector.
    ax.plot([0.602, 0.602, 0.204], [0.665, 0.585, 0.585], color=C["compress"],
            lw=0.8, ls=(0, (2, 2)))
    arrow(ax, (0.204, 0.585), (0.204, 0.427))
    ax.text(0.402, 0.593, "predicted winner", ha="center", va="bottom",
            fontsize=6.1, color=C["compress"])

    ax.text(0.50, 0.045,
            "$T_{ready}$ includes scan/probe and ends at a usable receiver tree; SHA-256 is measured separately.",
            ha="center", va="center", fontsize=6.35, color=C["gray"])
    fig.subplots_adjust(left=0.005, right=0.995, top=0.97, bottom=0.015)
    finish(fig, output, "fig1_architecture")


METHODS = ["gzip-6", "lz4", "zstd-3", "xz-6", "cpss-adaptive"]
METHOD_LABELS = ["gzip-6", "LZ4", "Zstd-3", "XZ-6", "CPSS"]


def method_pairs(rows: list[dict[str, str]], dataset: str, method: str
                 ) -> tuple[list[float], int, int, int, Counter[str]]:
    """Return paired Raw/method speedups and coverage/failure counts by repeat."""
    by_repeat: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    observed: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if row.get("dataset") != dataset or row.get("method") not in {"raw", method}:
            continue
        # Pair only within the same experiment run and repeat; merged CSVs may
        # reuse repeat numbers across independently launched experiment roots.
        repeat = row.get("repeat") or f"row-{index}"
        pair_id = f"{row.get('run_id', '')}::{repeat}"
        by_repeat[pair_id][row["method"]] = row
        if row["method"] == method:
            observed.append(row)

    raw_repeats = {key for key, values in by_repeat.items()
                   if "raw" in values and is_ok(values["raw"])}
    valid_repeats = {key for key, values in by_repeat.items()
                     if "raw" in values and key in raw_repeats and method in values
                     and is_ok(values[method])}
    ratios = [
        val(by_repeat[key]["raw"], "T_ready_s") / val(by_repeat[key][method], "T_ready_s")
        for key in valid_repeats
        if val(by_repeat[key]["raw"], "T_ready_s") > 0 and val(by_repeat[key][method], "T_ready_s") > 0
    ]
    failed = sum(1 for row in observed if row.get("status") != "completed"
                 or row.get("roundtrip_ok", "True").lower() == "false")
    actions = Counter(by_repeat[key][method].get("selected_action", "") for key in valid_repeats)
    return ratios, len(valid_repeats), len(raw_repeats), failed, actions


def fig_method_matrix(rows: list[dict[str, str]], output: Path) -> None:
    """Compare fixed methods and adaptive CPSS using paired E2E speedups."""
    fig = plt.figure(figsize=(6.35, 4.00))
    ax = fig.add_axes([0.205, 0.205, 0.645, 0.675])
    cmap = LinearSegmentedColormap.from_list(
        "raw_relative", ["#B85B4B", "#E9B9A9", "#F4F1E9", "#B9DCD2", "#267A70"], N=256
    )
    cmap.set_under("#914638")
    cmap.set_over("#125C53")
    norm = TwoSlopeNorm(vmin=0.5, vcenter=1.0, vmax=3.0)
    values = np.full((len(ORDER), len(METHODS)), np.nan)
    cells: dict[tuple[int, int], tuple[int, int, int, Counter[str]]] = {}
    for i, dataset in enumerate(ORDER):
        for j, method in enumerate(METHODS):
            ratios, n, n_raw, failed, actions = method_pairs(rows, dataset, method)
            cells[(i, j)] = (n, n_raw, failed, actions)
            if ratios:
                values[i, j] = statistics.median(ratios)

    # Failed/pending cells get a neutral background; color only encodes observed speedup.
    display_values = np.ma.masked_invalid(values)
    cmap.set_bad("#E6E6E6")
    ax.imshow(display_values, cmap=cmap, norm=norm, aspect="auto", interpolation="none")
    ax.set_xlim(-0.5, len(METHODS) - 0.5)
    ax.set_ylim(len(ORDER) - 0.5, -0.5)
    ax.set_xticks(np.arange(len(METHODS)), METHOD_LABELS)
    ax.set_yticks(np.arange(len(ORDER)),
                  [LABEL[d] + ("†" if d == "hurricane_isabel" else "") for d in ORDER])
    ax.tick_params(axis="both", length=0, pad=5)
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", pad=6)
    ax.set_xticklabels(METHOD_LABELS, fontsize=7.2)
    ax.set_yticklabels([LABEL[d] + ("†" if d == "hurricane_isabel" else "") for d in ORDER], fontsize=7.2)
    ax.set_xticks(np.arange(-0.5, len(METHODS), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(ORDER), 1), minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=1.6)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_title("End-to-end speedup relative to Raw", fontsize=9, loc="left", pad=13)

    for i in range(len(ORDER)):
        for j in range(len(METHODS)):
            n, n_raw, failed, actions = cells[(i, j)]
            if math.isfinite(values[i, j]):
                label = f"{values[i, j]:.2f}×"
                details = f"n={n}/{n_raw}"
                if j == len(METHODS) - 1:
                    action = "C" if actions.get("compress", 0) >= actions.get("raw", 0) else "R"
                    details += f" · {action}"
                if failed:
                    details += " · !"
                text_color = "white" if values[i, j] < 0.72 or values[i, j] > 2.0 else "#23312F"
                ax.text(j, i, f"{label}\n{details}", ha="center", va="center",
                        fontsize=6.2, color=text_color, linespacing=1.15)
            elif failed:
                ax.text(j, i, f"×\nfailed ({failed})", ha="center", va="center",
                        fontsize=6.2, color="#7D342A", linespacing=1.1)
            elif any(row.get("dataset") == ORDER[i] and row.get("method") == METHODS[j]
                     for row in rows):
                ax.text(j, i, "⋯\nin progress", ha="center", va="center",
                        fontsize=6.0, color="#686868", linespacing=1.1)
            else:
                ax.text(j, i, "—\npending", ha="center", va="center",
                        fontsize=6.0, color="#686868", linespacing=1.1)

    mappable = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cax = fig.add_axes([0.885, 0.31, 0.023, 0.48])
    cbar = fig.colorbar(mappable, cax=cax, extend="both")
    cbar.set_label("Speedup (×)", fontsize=7.0, labelpad=5)
    cbar.set_ticks([0.5, 0.75, 1, 1.5, 2, 2.5, 3])
    cbar.ax.tick_params(labelsize=6.2, length=2, width=0.5)
    cbar.outline.set_linewidth(0.6)
    fig.text(0.205, 0.102,
             ">1 = faster than Raw; cell = median paired $T_{ready}^{Raw}/T_{ready}^{method}$, n = valid pairs / Raw runs.",
             ha="left", va="center", fontsize=6.25, color=C["gray"])
    fig.text(0.205, 0.063,
             "CPSS: C = compress, R = Raw bypass; ! = failed attempts.  × = all attempts failed; — = pending.  † Hurricane is pre-fix.",
             ha="left", va="center", fontsize=6.0, color=C["gray"])
    finish(fig, output, "fig2_method_comparison")


def cp_components(row: dict[str, str]) -> dict[str, float]:
    setup_known = val(row, "scan_time_s") + val(row, "probe_time_s") + val(row, "fallback_overhead_s")
    encode = val(row, "compress_time_s")
    transfer = val(row, "gridflux_transfer_time_s")
    restore = val(row, "remote_decompress_time_s") + val(row, "remote_rebuild_time_s")
    # Keep all remaining measured T_ready cost visible, rather than forcing components to sum approximately.
    setup = max(0.0, val(row, "T_ready_s") - encode - transfer - restore)
    return {"decision_setup": setup, "encode": encode, "transfer": transfer, "restore": restore}


def fig_normalized_budget(rows: list[dict[str, str]], output: Path) -> None:
    fig = plt.figure(figsize=(6.35, 4.05))
    ax = fig.add_axes([0.20, 0.15, 0.57, 0.78])
    yy = np.arange(len(ORDER))[::-1]
    ax.set_xlim(0, 1.35)
    ax.set_ylim(-0.6, len(ORDER) - 0.4)
    ax.axvline(1, color="#505050", lw=0.8, ls=(0, (3, 2)), zorder=0)
    ax.text(1.01, len(ORDER) - 0.25, "Raw = 1", ha="left", va="top", fontsize=6.8, color=C["gray"])
    ax.grid(axis="x", color=C["grid"], lw=0.55)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#777777")
    ax.spines[["left", "bottom"]].set_linewidth(0.65)
    ax.set_yticks(yy, [LABEL[d] + ("†" if d == "hurricane_isabel" else "") for d in ORDER])
    ax.set_xlabel("CPSS time, normalized by paired Raw $T_{ready}$")
    ax.tick_params(length=2.5, width=0.6, color="#555555")

    for i, dataset in enumerate(ORDER):
        y = yy[i]
        pairs = paired(rows, dataset)
        if not pairs:
            ax.text(0.025, y, "CPSS pending", ha="left", va="center", fontsize=6.8, color=C["gray"])
            continue
        component_by_run: dict[str, list[float]] = {c: [] for c in COMPONENTS}
        for raw, cp in pairs:
            raw_t = val(raw, "T_ready_s")
            if raw_t <= 0:
                continue
            for component, value in cp_components(cp).items():
                component_by_run[component].append(value / raw_t)
        left = 0.0
        for component in COMPONENTS:
            arr = component_by_run[component]
            width = statistics.mean(arr) if arr else 0.0
            if width > 0:
                ax.barh(y, width, left=left, height=0.50, color=COMPONENT_COLOR[component],
                        edgecolor="white", linewidth=0.35, zorder=2)
            left += width

    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=COMPONENT_COLOR[k], edgecolor="none",
                             label=COMPONENT_LABEL[k]) for k in COMPONENTS]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.58, -0.27),
              ncol=2, frameon=False, columnspacing=1.2, handlelength=1.2, handletextpad=0.45)
    fig.text(0.20, 0.025,
             "Bars show mean paired phase time / Raw time; endpoint is mean CPSS/Raw.  † pre-fix Hurricane.",
             ha="left", va="center", fontsize=6.2, color=C["gray"])
    finish(fig, output, "fig3_normalized_time_budget")


def fig_absolute_breakdown(rows: list[dict[str, str]], output: Path) -> None:
    selected = ["cesm_atm", "exafel", "miranda", "rcsb_mmcif"]
    fig, axes = plt.subplots(2, 2, figsize=(6.35, 4.2))
    for ax, dataset in zip(axes.flat, selected):
        raw_rows = rows_for(rows, dataset, "raw")
        cp_rows = rows_for(rows, dataset, "cpss-adaptive")
        if not raw_rows or not cp_rows:
            ax.set_axis_off()
            ax.set_title(f"{LABEL[dataset]} (pending)", loc="left", fontsize=8)
            continue
        raw_mean = statistics.mean(val(r, "T_ready_s") for r in raw_rows)
        means = {component: statistics.mean(cp_components(r)[component] for r in cp_rows)
                 for component in COMPONENTS}
        cp_mean = sum(means.values())
        y_raw, y_cp = 0.5, 1.25
        ax.barh(y_raw, raw_mean, height=0.36, color="#B8B8B8", edgecolor="none", label="Raw")
        left = 0.0
        for component in COMPONENTS:
            width = means[component]
            ax.barh(y_cp, width, left=left, height=0.36, color=COMPONENT_COLOR[component],
                    edgecolor="white", linewidth=0.3, label=COMPONENT_LABEL[component])
            left += width
        max_v = max(raw_mean, cp_mean)
        ax.text(raw_mean + max_v * 0.025, y_raw, f"{raw_mean:.1f} s", va="center", ha="left", fontsize=6.8)
        ax.text(cp_mean + max_v * 0.025, y_cp, f"{cp_mean:.1f} s", va="center", ha="left", fontsize=6.8)
        ax.set_xlim(0, max_v * 1.25)
        ax.set_ylim(0.15, 1.65)
        ax.set_yticks([y_raw, y_cp], ["Raw", "CPSS"])
        ax.set_title(LABEL[dataset], loc="left", fontsize=8.5, pad=3)
        ax.grid(axis="x", color=C["grid"], lw=0.5)
        ax.set_axisbelow(True)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color("#777777")
        ax.spines["bottom"].set_linewidth(0.6)
        ax.tick_params(axis="y", length=0, pad=3)
        ax.tick_params(axis="x", length=2, width=0.55, color="#555555")

    axes[0, 0].set_ylabel("Mean time (s)", labelpad=2)
    axes[1, 0].set_ylabel("Mean time (s)", labelpad=2)
    axes[1, 0].set_xlabel("End-to-end time (s)")
    axes[1, 1].set_xlabel("End-to-end time (s)")
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor="#B8B8B8", edgecolor="none", label="Raw"),
               *[plt.Rectangle((0, 0), 1, 1, facecolor=COMPONENT_COLOR[k], edgecolor="none",
                               label=COMPONENT_LABEL[k]) for k in COMPONENTS]]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.005),
               ncol=3, frameon=False, columnspacing=1.2, handlelength=1.2, handletextpad=0.45)
    fig.text(0.03, 0.005,
             "Bars are means over completed repetitions; per-run CPSS components sum to $T_{ready}$. Each panel has its own time scale.",
             ha="left", va="bottom", fontsize=6.1, color=C["gray"])
    fig.subplots_adjust(left=0.12, right=0.98, top=0.96, bottom=0.20, hspace=0.35, wspace=0.30)
    finish(fig, output, "fig4_absolute_time_breakdown")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    style()
    data = load(args.csv)
    fig_architecture(args.output)
    fig_method_matrix(data, args.output)
    fig_normalized_budget(data, args.output)
    fig_absolute_breakdown(data, args.output)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Create an IEEE-style monochrome copy of the editable CPSS architecture."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon
from matplotlib.path import Path as MplPath


ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs" / "dcc_latex" / "cpss_submission" / "Figures"
SOURCE_DRAWIO = FIGURES / "cpss_architecture.drawio"
OUTPUT_DRAWIO = FIGURES / "cpss_architecture_ieee_bw.drawio"
SOURCE_PNG = FIGURES / "cpss_architecture.png"
OUTPUT_PNG = FIGURES / "cpss_architecture_ieee_bw.png"
OUTPUT_PDF = FIGURES / "cpss_architecture_ieee_bw.pdf"


COLOR_MAP = {
    "#F1F7FA": "#F5F5F5",
    "#FCFCFB": "#FFFFFF",
    "#284354": "#111111",
    "#E2EDF3": "#EEEEEE",
    "#345C73": "#111111",
    "#FFFFFF": "#FFFFFF",
    "#5AA5CD": "#111111",
    "#667983": "#4A4A4A",
    "#397DA5": "#111111",
    "#203746": "#111111",
    "#526773": "#3E3E3E",
    "#778790": "#111111",
    "#EAF2F6": "#EEEEEE",
    "#E5F2F2": "#F0F0F0",
    "#16807D": "#111111",
    "#55737D": "#484848",
    "#FFF0E6": "#FFFFFF",
    "#CF6B27": "#333333",
    "#7B6657": "#4A4A4A",
    "#F4F1E7": "#E5E5E5",
    "#B38A32": "#111111",
    "#C7CDD0": "#777777",
    "#F8F0F6": "#EEEEEE",
    "#A86C99": "#111111",
    "#766176": "#4A4A4A",
    "#677780": "#4A4A4A",
    "#71838D": "#333333",
}


def monochrome_drawio() -> None:
    original = SOURCE_DRAWIO.read_text(encoding="utf-8")
    updated = original
    for old, new in COLOR_MAP.items():
        updated = updated.replace(old, new)

    updated = updated.replace("Estimate time to usable data", "Estimate candidate cost")
    updated = updated.replace("sample-bundle path cost", "sample encode/decode")
    updated = updated.replace("Fixed transfer path", "Transfer path")
    updated = updated.replace("probe + preparation + wire + restore", "sample encode + payload wire + decode")
    updated = updated.replace("sample encode + payload wire + decode", "sample encode + payload wire&lt;br&gt;+ decode")
    updated = updated.replace("seeded, bounded input regions", "seeded, bounded windows")
    updated = updated.replace(
        "output bytes · encode/decode&lt;br&gt;sample encode/decode",
        "encoded size · encode/decode",
    )
    updated = updated.replace("estimated time", "estimated cost")
    updated = updated.replace(
        "Both actions share the configured transfer and receiver path",
        "Shared transfer and receiver path",
    )

    # Keep the original geometry and distinguish the Raw branch with a dashed
    # outline in print, where the source figure's color distinction disappears.
    updated = updated.replace(
        "id=\"raw\" value=\"&lt;b&gt;Raw bypass&lt;/b&gt;&lt;br&gt;&lt;font style=&quot;font-size:14px;color:#4A4A4A&quot;&gt;original tree, unchanged&lt;/font&gt;\" style=\"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#333333;strokeWidth=2;",
        "id=\"raw\" value=\"&lt;b&gt;Raw bypass&lt;/b&gt;&lt;br&gt;&lt;font style=&quot;font-size:14px;color:#4A4A4A&quot;&gt;original tree, unchanged&lt;/font&gt;\" style=\"rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#333333;strokeWidth=2;dashed=1;dashPattern=4 4;",
    )
    updated = updated.replace(
        'id="e-decision-raw" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#333333;strokeWidth=3;endArrow=block;',
        'id="e-decision-raw" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#333333;strokeWidth=3;dashed=1;dashPattern=4 4;endArrow=block;',
    )
    updated = updated.replace(
        'id="e-raw-transfer" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#333333;strokeWidth=2;endArrow=block;',
        'id="e-raw-transfer" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#333333;strokeWidth=2;dashed=1;dashPattern=4 4;endArrow=block;',
    )

    # Assert that this is a style-only variant: cell IDs and geometry must be
    # byte-identical after parsing, so the user's adjusted layout is retained.
    old_root = ET.fromstring(original)
    new_root = ET.fromstring(updated)
    old_cells = {cell.get("id"): cell for cell in old_root.iter("mxCell")}
    new_cells = {cell.get("id"): cell for cell in new_root.iter("mxCell")}
    if old_cells.keys() != new_cells.keys():
        raise RuntimeError("draw.io cell identities changed during monochrome restyling")
    for cell_id, old_cell in old_cells.items():
        old_geometry = old_cell.find("mxGeometry")
        new_geometry = new_cells[cell_id].find("mxGeometry")
        if (ET.tostring(old_geometry) if old_geometry is not None else None) != (
            ET.tostring(new_geometry) if new_geometry is not None else None
        ):
            raise RuntimeError(f"draw.io geometry changed for cell {cell_id}")
    OUTPUT_DRAWIO.write_text(updated, encoding="utf-8")


def monochrome_preview() -> None:
    fig = plt.figure(figsize=(14, 6), facecolor="white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1400)
    ax.set_ylim(600, 0)
    ax.set_aspect("auto")
    ax.axis("off")

    def round_box(x: float, y: float, w: float, h: float, *, fill: str = "white",
                  edge: str = "#111111", lw: float = 1.35, dashed: bool = False) -> None:
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.012,rounding_size=10",
            linewidth=lw, edgecolor=edge, facecolor=fill,
            linestyle="--" if dashed else "-", zorder=3,
        ))

    def label(x: float, y: float, w: float, h: float, title: str, detail: str = "",
              *, title_size: float = 14.2, detail_size: float = 10.8,
              fill: str = "white", edge: str = "#111111", lw: float = 1.35,
              dashed: bool = False) -> None:
        round_box(x, y, w, h, fill=fill, edge=edge, lw=lw, dashed=dashed)
        center_x = x + w / 2
        if detail:
            title_y = y + h * .38
            detail_y = y + h * .67
            ax.text(center_x, title_y, title, ha="center", va="center",
                    fontsize=title_size, fontweight="bold", color="#111111", zorder=5)
            ax.text(center_x, detail_y, detail, ha="center", va="center",
                    fontsize=detail_size, color="#303030", linespacing=1.18, zorder=5)
        else:
            ax.text(center_x, y + h / 2, title, ha="center", va="center",
                    fontsize=title_size, fontweight="bold", color="#111111", zorder=5)

    def arrow(points: list[tuple[float, float]], *, dashed: bool = False,
              linewidth: float = 1.5, mutation: float = 12) -> None:
        vertices = points
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(vertices) - 1)
        path = MplPath(vertices, codes)
        ax.add_patch(FancyArrowPatch(
            path=path, arrowstyle="-|>", mutation_scale=mutation,
            linewidth=linewidth, color="#111111",
            linestyle="--" if dashed else "-", zorder=2,
        ))

    # The same section and node geometry as the user-edited draw.io source.
    ax.add_patch(FancyBboxPatch((25, 25), 1080, 205,
                                boxstyle="round,pad=0.012,rounding_size=16",
                                linewidth=0, facecolor="#F5F5F5", zorder=0))
    ax.add_patch(FancyBboxPatch((25, 250), 1350, 315,
                                boxstyle="round,pad=0.012,rounding_size=16",
                                linewidth=0, facecolor="#FFFFFF", zorder=0))
    ax.text(55, 53, "BOUNDED PROBING  ·  COST ESTIMATION", ha="left", va="center",
            fontsize=18.5, fontweight="bold", color="#111111", zorder=5)
    round_box(765, 38, 300, 30, fill="#E6E6E6", edge="none", lw=0)
    ax.text(915, 53, "Probe time is charged to CPSS", ha="center", va="center",
            fontsize=12.0, fontweight="bold", color="#111111", zorder=5)
    ax.text(55, 280, "FULL-DATA PATH", ha="left", va="center", fontsize=18.5,
            fontweight="bold", color="#111111", zorder=5)

    # Edges are drawn first; dashed routes denote sampling/decision feedback
    # and the Raw bypass branch, so the diagram remains interpretable in print.
    arrow([(490, 138), (565, 138)])
    arrow([(790, 138), (820, 138)])
    arrow([(140, 385), (140, 330), (250, 330), (250, 138), (285, 138)], dashed=True)
    arrow([(245, 426), (315, 422)])
    arrow([(952.5, 184), (952.5, 235), (402.5, 235), (402.5, 355)], dashed=True)
    arrow([(490, 402), (520, 402), (520, 356), (555, 356)], linewidth=1.7)
    arrow([(490, 450), (520, 450), (520, 476), (555, 476)], dashed=True, linewidth=1.7)
    arrow([(775, 356), (810, 356), (810, 415), (855, 415)])
    arrow([(775, 476), (810, 476), (810, 443), (855, 443)], dashed=True)
    arrow([(1060, 429), (1150, 429)], linewidth=1.7)
    ax.plot([1110, 1110], [290, 525], color="#666666", linewidth=1.05,
            linestyle=(0, (3, 3)), zorder=1)

    label(285, 92, 205, 92, "Sample windows", "seeded, bounded windows", detail_size=9.7)
    label(565, 92, 225, 92, "Profile candidates", "encoded size · encode/decode", detail_size=10.0)
    label(820, 92, 265, 92, "Estimate candidate cost",
          "sample encode + payload wire\n+ decode", title_size=13.5, detail_size=9.8)
    label(55, 385, 190, 82, "INPUT TREE", "full source payload")
    ax.add_patch(Polygon([(402.5, 355), (490, 422.5), (402.5, 490), (315, 422.5)],
                         closed=True, facecolor="#E8E8E8", edgecolor="#111111",
                         linewidth=1.35, zorder=3))
    ax.text(402.5, 405, "Choose lower", ha="center", va="center", fontsize=12.8,
            fontweight="bold", color="#111111", zorder=5)
    ax.text(402.5, 428, "estimated cost", ha="center", va="center", fontsize=12.8,
            fontweight="bold", color="#111111", zorder=5)
    ax.text(402.5, 442, "Raw / compress", ha="center", va="center", fontsize=9.0,
            color="#303030", zorder=5)
    label(555, 315, 220, 82, "Compress & bundle", "selected codec over full data",
          fill="#E8E8E8", title_size=14.2, detail_size=10.4)
    label(555, 435, 220, 82, "Raw bypass", "original tree, unchanged",
          fill="#FFFFFF", title_size=14.2, detail_size=10.4, dashed=True)
    label(855, 383, 205, 92, "Transfer path", "selected representation\n+ bundle metadata",
          fill="#E5E5E5", title_size=12.2, detail_size=9.6)
    label(1150, 383, 200, 92, "USABLE TREE", "decode/rebuild bundle;\nmaterialize Raw otherwise",
          fill="#EEEEEE", title_size=13.8, detail_size=9.5)

    ax.text(200, 318, "sample only", ha="center", va="center", fontsize=10.5,
            fontweight="bold", color="#222222", zorder=5)
    ax.text(678, 226, "predicted lower-cost action", ha="center", va="center",
            fontsize=10.8, fontweight="bold", color="#222222",
            bbox={"facecolor": "#F5F5F5", "edgecolor": "none", "pad": 2.0}, zorder=5)
    ax.text(957, 535, "Shared transfer and receiver path",
            ha="center", va="center", fontsize=9.7, color="#444444", zorder=5)

    fig.savefig(OUTPUT_PDF, facecolor="white", bbox_inches=None, pad_inches=0)
    fig.savefig(OUTPUT_PNG, facecolor="white", dpi=300, bbox_inches=None, pad_inches=0)
    plt.close(fig)


if __name__ == "__main__":
    monochrome_drawio()
    monochrome_preview()
    print(f"Created {OUTPUT_DRAWIO}")
    print(f"Created {OUTPUT_PNG}")
    print(f"Created {OUTPUT_PDF}")

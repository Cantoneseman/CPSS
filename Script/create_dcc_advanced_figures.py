"""Create the redesigned color schematics for the CPSS DCC paper.

The diagrams use a small, color-vision-safe palette with fixed semantics:
blue is observation/decision, teal is a selected compression action, orange
is probing or Raw bypass, purple is receiver reconstruction, and yellow is
the transport path.  The same diagrams are exported as PNG/PDF/EPS and as
editable draw.io sources.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle


P = {
    "ink": "#243447",
    "muted": "#5D6B78",
    "grid": "#D7E0E7",
    "navy": "#1F3A5F",
    "blue": "#0072B2",       # observation / decision
    "sky": "#56B4E9",        # probe details
    "teal": "#009E73",       # selected action / CPSS
    "orange": "#D55E00",     # Raw bypass / probe emphasis
    "gold": "#E69F00",       # network transfer
    "purple": "#CC79A7",     # receiver recovery
    "yellow": "#F0E442",
    "blue_fill": "#E8F2F8",
    "sky_fill": "#EEF8FC",
    "teal_fill": "#E8F5F0",
    "orange_fill": "#FFF0E8",
    "gold_fill": "#FFF7DE",
    "purple_fill": "#F9EFF6",
    "neutral_fill": "#F7F9FB",
}


def style() -> None:
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["STIX Two Text", "STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 7.3,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })


def save(fig: plt.Figure, output: Path, stem: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight", metadata={"Title": stem})
    fig.savefig(output / f"{stem}.eps", bbox_inches="tight")
    fig.savefig(output / f"{stem}.png", dpi=500, bbox_inches="tight")
    plt.close(fig)


def arrow(ax: plt.Axes, a: tuple[float, float], b: tuple[float, float], *,
          color: str = P["muted"], lw: float = 0.85, ls: str = "-",
          rad: float = 0.0, mutation: float = 8.0) -> None:
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=mutation,
                                 linewidth=lw, linestyle=ls, color=color,
                                 connectionstyle=f"arc3,rad={rad}",
                                 shrinkA=2, shrinkB=2, zorder=8))


def panel(ax: plt.Axes, x: float, y: float, w: float, h: float, title: str,
          number: str, fill: str) -> None:
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=P["grid"],
                           linewidth=0.8, zorder=0))
    ax.add_patch(Rectangle((x, y + h - 0.047), w, 0.047, facecolor=P["navy"],
                           edgecolor=P["navy"], linewidth=0.0, zorder=1))
    ax.text(x + 0.012, y + h - 0.023, f"{number}  {title}", ha="left", va="center",
            fontsize=7.0, fontweight="bold", color="white", zorder=2)


def card(ax: plt.Axes, x: float, y: float, w: float, h: float, title: str,
         detail: str, edge: str, face: str, *, title_size: float = 7.1,
         detail_size: float = 5.7) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.003,rounding_size=0.006",
                                facecolor=face, edgecolor=edge, linewidth=0.8, zorder=4))
    ax.text(x + w / 2, y + h * 0.63, title, ha="center", va="center",
            fontsize=title_size, fontweight="bold", color=P["ink"], zorder=5)
    ax.text(x + w / 2, y + h * 0.28, detail, ha="center", va="center",
            fontsize=detail_size, color=P["muted"], linespacing=1.05, zorder=5)


def pill(ax: plt.Axes, x: float, y: float, w: float, text: str, color: str,
         face: str) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, 0.034,
                                boxstyle="round,pad=0.002,rounding_size=0.012",
                                facecolor=face, edgecolor=color, linewidth=0.65, zorder=4))
    ax.text(x + w / 2, y + 0.017, text, ha="center", va="center", fontsize=5.6,
            color=P["ink"], zorder=5)


def architecture(output: Path) -> None:
    """A layered system view: observe, cost, choose, execute, restore."""
    fig, ax = plt.subplots(figsize=(7.05, 3.72))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.02, 0.982, "CPSS: a costed decision loop around a concrete transfer path",
            ha="left", va="top", fontsize=9.5, fontweight="bold", color=P["ink"])
    ax.text(0.98, 0.982, "one-shot scientific/HPC transfer", ha="right", va="top",
            fontsize=6.0, color=P["muted"])

    # Three aligned system planes make the figure denser without creating a
    # wall of unrelated boxes.
    panel(ax, 0.02, 0.12, 0.235, 0.78, "Observe the workload", "I", P["neutral_fill"])
    panel(ax, 0.275, 0.12, 0.365, 0.78, "Estimate and choose", "II", P["blue_fill"])
    panel(ax, 0.66, 0.12, 0.32, 0.78, "Execute and restore", "III", P["neutral_fill"])

    # I: source object and bounded evidence.
    card(ax, 0.038, 0.68, 0.215, 0.125, "Input bundle", "file tree  |  raw bytes  |  metadata",
         P["navy"], "white", title_size=7.2)
    for idx, (label, color, fill) in enumerate([
        ("float", P["blue"], P["blue_fill"]),
        ("text", P["teal"], P["teal_fill"]),
        ("blocks", P["gold"], P["gold_fill"]),
    ]):
        x = 0.050 + idx * 0.065
        ax.add_patch(Rectangle((x, 0.684), 0.047, 0.018, facecolor=fill,
                               edgecolor=color, linewidth=0.7, zorder=5))
        ax.text(x + 0.0235, 0.693, label, ha="center", va="center", fontsize=4.25,
                color=P["ink"], zorder=6)
    card(ax, 0.038, 0.49, 0.215, 0.125, "Bounded probe", "4 MiB aligned  |  at most two windows",
         P["sky"], P["sky_fill"], title_size=7.2)
    # Window glyphs and an explicit sampling budget.
    for idx, x in enumerate([0.058, 0.095, 0.132, 0.169, 0.206]):
        ax.add_patch(Rectangle((x, 0.493), 0.025, 0.021,
                               facecolor="white" if idx not in (1, 3) else P["sky"],
                               edgecolor=P["sky"], linewidth=0.65, zorder=5))
    arrow(ax, (0.145, 0.68), (0.145, 0.615), color=P["blue"], lw=0.8)
    arrow(ax, (0.145, 0.49), (0.145, 0.425), color=P["sky"], lw=0.8)
    card(ax, 0.038, 0.25, 0.215, 0.165, "Feature record", "byte stats  |  deltas  |  type hints",
         P["blue"], "white", title_size=7.2)
    for idx, (label, color) in enumerate([("entropy", P["blue"]), ("delta", P["teal"]), ("type", P["purple"])]) :
        pill(ax, 0.052 + idx * 0.064, 0.251, 0.057, label, color, "white")
    ax.text(0.145, 0.19, "evidence is small; payload stays untouched", ha="center", va="center",
            fontsize=5.9, color=P["muted"], style="italic")

    # II: feature vector, candidate pool and costed decision.
    ax.text(0.295, 0.825, "feature vector  x", ha="left", va="center", fontsize=6.4,
            fontweight="bold", color=P["blue"])
    for idx, (label, color, fill) in enumerate([
        ("byte", P["blue"], P["blue_fill"]), ("delta", P["teal"], P["teal_fill"]),
        ("float", P["purple"], P["purple_fill"]), ("shape", P["gold"], P["gold_fill"]),
        ("probe", P["sky"], P["sky_fill"]),
    ]):
        pill(ax, 0.295 + idx * 0.065, 0.775, 0.057, label, color, fill)
    arrow(ax, (0.39, 0.75), (0.39, 0.695), color=P["blue"], lw=0.8)
    card(ax, 0.295, 0.58, 0.325, 0.115, "Candidate actions", "",
         P["teal"], P["teal_fill"], title_size=7.2)
    for idx, (label, color, face) in enumerate([
        ("Raw", P["orange"], P["orange_fill"]), ("Zstd-3", P["blue"], P["blue_fill"]),
        ("LZ4", P["gold"], P["gold_fill"]), ("delta", P["purple"], P["purple_fill"]),
    ]):
        pill(ax, 0.309 + idx * 0.074, 0.592, 0.066, label, color, face)
    arrow(ax, (0.458, 0.58), (0.458, 0.515), color=P["teal"], lw=0.8)
    # Cost model with the equation and the actual decision variables.
    ax.add_patch(FancyBboxPatch((0.295, 0.285), 0.325, 0.20,
                                boxstyle="round,pad=0.004,rounding_size=0.007",
                                facecolor="white", edgecolor=P["blue"], linewidth=1.0, zorder=4))
    ax.text(0.313, 0.455, "end-to-end cost model", ha="left", va="center", fontsize=7.1,
            fontweight="bold", color=P["ink"], zorder=5)
    ax.text(0.458, 0.405,
            r"$T_{ready}(a)=T_{probe}+T_{enc}(a)+S(a)/B+T_{dec}(a)+T_{rebuild}(a)$",
            ha="center", va="center", fontsize=6.2, color=P["navy"], zorder=5)
    ax.text(0.313, 0.345, "a: action     B: configured bandwidth     S(a): wire bytes",
            ha="left", va="center", fontsize=5.6, color=P["muted"], zorder=5)
    ax.text(0.313, 0.315, r"choose $\arg\min_a T_{ready}(a)$; retain Raw as a valid action",
            ha="left", va="center", fontsize=5.6, color=P["teal"], zorder=5)
    arrow(ax, (0.458, 0.285), (0.458, 0.23), color=P["blue"], lw=0.8)
    ax.add_patch(Polygon([[0.458, 0.23], [0.505, 0.188], [0.458, 0.146], [0.411, 0.188]],
                         closed=True, facecolor=P["teal_fill"], edgecolor=P["teal"],
                         linewidth=1.0, zorder=4))
    ax.text(0.458, 0.195, "select", ha="center", va="center", fontsize=7.2,
            fontweight="bold", color=P["ink"], zorder=5)
    ax.text(0.458, 0.169, "Raw / codec", ha="center", va="center", fontsize=5.3,
            color=P["muted"], zorder=5)

    # III: concrete branches converge on one wire and one restore contract.
    card(ax, 0.681, 0.685, 0.278, 0.12, "Selected action", "",
         P["teal"], P["teal_fill"], title_size=7.2)
    for idx, (label, color, face) in enumerate([
        ("compress", P["teal"], P["teal_fill"]), ("bypass", P["orange"], P["orange_fill"]),
        ("manifest", P["blue"], P["blue_fill"]),
    ]):
        pill(ax, 0.698 + idx * 0.083, 0.713, 0.074, label, color, face)
    ax.text(0.82, 0.695, "policy output  |  codec + level + bypass flag",
            ha="center", va="center", fontsize=5.35, color=P["muted"])
    arrow(ax, (0.82, 0.685), (0.82, 0.625), color=P["teal"], lw=0.8)
    # two action cards make the conditional nature explicit.
    card(ax, 0.681, 0.495, 0.127, 0.105, "Encode", "payload + manifest\nCRC32C", P["teal"], P["teal_fill"], title_size=6.8, detail_size=5.6)
    card(ax, 0.832, 0.495, 0.127, 0.105, "Raw bypass", "original bytes\nno codec", P["orange"], P["orange_fill"], title_size=6.8, detail_size=5.6)
    arrow(ax, (0.82, 0.625), (0.744, 0.60), color=P["teal"], lw=0.8)
    arrow(ax, (0.82, 0.625), (0.895, 0.60), color=P["orange"], lw=0.8)
    # A transport spine and receiver contract fill the lower half.
    ax.add_patch(Rectangle((0.681, 0.38), 0.278, 0.06, facecolor=P["gold_fill"],
                           edgecolor=P["gold"], linewidth=0.9, zorder=4))
    ax.text(0.82, 0.414, "cross-domain transfer  ·  payload + metadata  ·  configured link",
            ha="center", va="center", fontsize=5.8, fontweight="bold", color=P["ink"], zorder=5)
    arrow(ax, (0.744, 0.495), (0.744, 0.44), color=P["gold"], lw=0.8)
    arrow(ax, (0.895, 0.495), (0.895, 0.44), color=P["gold"], lw=0.8)
    card(ax, 0.681, 0.205, 0.278, 0.12, "Destination-ready result", "verify manifest + CRC32C  |  decode  |  rebuild tree",
         P["purple"], P["purple_fill"], title_size=7.2)
    arrow(ax, (0.82, 0.38), (0.82, 0.325), color=P["purple"], lw=0.8)
    ax.text(0.82, 0.16, "same T_ready boundary for Raw, fixed codecs, and CPSS",
            ha="center", va="center", fontsize=5.8, color=P["muted"], style="italic")

    # Cross-plane links and one explicit feedback-like probe relation.
    arrow(ax, (0.255, 0.35), (0.295, 0.35), color=P["blue"], lw=0.9)
    arrow(ax, (0.64, 0.188), (0.681, 0.75), color=P["teal"], lw=0.8, ls=(0, (2, 2)), rad=-0.08)
    ax.text(0.653, 0.48, "selected\naction", ha="center", va="center", rotation=72,
            fontsize=5.4, color=P["teal"])

    ax.text(0.02, 0.045,
            "Color semantics: blue = evidence/decision  ·  teal = compression action  ·  orange = probe/Raw  ·  yellow = wire  ·  purple = restore",
            ha="left", va="center", fontsize=5.6, color=P["muted"])
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    save(fig, output, "cpss_architecture_color")


def timing_boundary(output: Path) -> None:
    """A richer aligned timeline that makes the matched timing boundary explicit."""
    fig, ax = plt.subplots(figsize=(7.05, 2.65))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(0.02, 0.972, "A matched $T_{ready}$ boundary makes the comparison end-to-end",
            ha="left", va="top", fontsize=9.2, fontweight="bold", color=P["ink"])
    ax.text(0.98, 0.972, "same input bundle  ·  same configured link  ·  destination-ready output",
            ha="right", va="top", fontsize=5.8, color=P["muted"])

    x0, x1 = 0.22, 0.94
    cols = [
        (x0, 0.105, "probe / scan", P["sky"], P["sky_fill"]),
        (x0 + 0.105, 0.185, "full-data\nencode", P["teal"], P["teal_fill"]),
        (x0 + 0.290, 0.285, "cross-domain\ntransfer", P["gold"], P["gold_fill"]),
        (x0 + 0.575, 0.145, "decode /\nrebuild", P["purple"], P["purple_fill"]),
    ]
    for left, width, label, edge, face in cols:
        ax.add_patch(Rectangle((left, 0.785), width, 0.06, facecolor=face,
                               edgecolor=edge, linewidth=0.7, zorder=2))
        ax.text(left + width / 2, 0.815, label, ha="center", va="center", fontsize=6.0,
                color=P["ink"], zorder=3)
    ax.text(0.58, 0.855, "shared phase vocabulary", ha="center", va="bottom", fontsize=5.7,
            color=P["muted"], style="italic")

    rows = [
        ("Raw", 0.63, [(2, "original bytes"), (3, "destination ready")], P["orange"]),
        ("Fixed codec", 0.45, [(1, "encode"), (2, "compressed bytes"), (3, "decode / rebuild")], P["blue"]),
        ("CPSS", 0.27, [(0, "bounded probe"), (1, "conditional encode"), (2, "selected payload"), (3, "restore")], P["teal"]),
    ]
    for name, y, active, label_color in rows:
        ax.text(0.02, y + 0.025, name, ha="left", va="center", fontsize=7.6,
                fontweight="bold", color=label_color)
        ax.plot([x0, x1], [y, y], color=P["grid"], lw=0.45, zorder=0)
        active_map = {i: label for i, label in active}
        for i, (left, width, phase, edge, face) in enumerate(cols):
            if i not in active_map:
                ax.add_patch(Rectangle((left, y - 0.0525), width, 0.105,
                                       facecolor="#FBFCFD", edgecolor=P["grid"],
                                       linewidth=0.45, linestyle=(0, (2, 2)), zorder=1))
                continue
            active_face = face
            if name == "Raw" and i == 2:
                active_face = P["orange_fill"]
            if name == "Fixed codec" and i == 1:
                active_face = P["blue_fill"]
            if name == "CPSS" and i == 0:
                active_face = P["sky_fill"]
            ax.add_patch(Rectangle((left, y - 0.0525), width, 0.105,
                                   facecolor=active_face, edgecolor=edge,
                                   linewidth=0.9, zorder=2))
            ax.text(left + width / 2, y, active_map[i], ha="center", va="center",
                    fontsize=6.2, color=P["ink"], zorder=3)
        # The bracket is the quantity compared by every method.
        left = x0 if name == "CPSS" else (cols[2][0] if name == "Raw" else cols[1][0])
        right = x1
        yb = y + 0.085
        ax.plot([left, right], [yb, yb], color=label_color, lw=0.75, zorder=4)
        ax.plot([left, left], [yb - 0.012, yb + 0.012], color=label_color, lw=0.75, zorder=4)
        ax.plot([right, right], [yb - 0.012, yb + 0.012], color=label_color, lw=0.75, zorder=4)
        ax.text((left + right) / 2, yb + 0.013, "$T_{ready}$", ha="center", va="bottom",
                fontsize=6.5, color=label_color, fontweight="bold")

    # Probe overhead is visible as a separate, measured part of the CPSS row.
    ax.annotate("decision overhead", xy=(cols[0][0] + 0.05, 0.305),
                xytext=(0.10, 0.22), ha="center", va="center", fontsize=5.6,
                color=P["orange"], arrowprops={"arrowstyle": "-|>", "color": P["orange"], "lw": 0.7})
    ax.text(0.22, 0.07,
            "Empty cells are bypassed phases; the CPSS probe is counted because the decision is part of the transfer workflow.",
            ha="left", va="center", fontsize=5.7, color=P["muted"])
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.02)
    save(fig, output, "timing_boundary_color")


def drawio_architecture_editable_xml() -> str:
    """Large, readable editing draft for draw.io.

    The publication PNG is intentionally dense; this source uses a larger
    canvas and shorter labels so that a human can rearrange modules without
    zooming into tiny text.
    """
    cells = [
        ("title", "CPSS: cost-aware compression decision for end-to-end transfer", 45, 25, 1480, 48,
         "text;html=1;strokeColor=none;fillColor=none;fontStyle=1;fontSize=26;fontColor=#243447;"),
        ("h1", "I. OBSERVE THE WORKLOAD", 45, 105, 360, 48,
         "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;fontSize=18;"),
        ("h2", "II. ESTIMATE AND CHOOSE", 445, 105, 600, 48,
         "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;fontSize=18;"),
        ("h3", "III. EXECUTE AND RESTORE", 1085, 105, 470, 48,
         "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;fontSize=18;"),
        ("in", "INPUT BUNDLE\nfile tree | raw bytes | metadata", 80, 205, 290, 105,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;fontSize=17;"),
        ("probe", "BOUNDED PROBE\n4 MiB aligned | at most two windows", 80, 350, 290, 105,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#EEF8FC;strokeColor=#56B4E9;fontColor=#243447;fontSize=17;"),
        ("feat", "FEATURE RECORD\nbyte statistics | local deltas | type hints", 80, 495, 290, 105,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;fontSize=17;"),
        ("vec", "FEATURE VECTOR x\nbyte | delta | float | shape | probe", 505, 205, 480, 92,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F2F8;strokeColor=#0072B2;fontColor=#243447;fontStyle=1;fontSize=17;"),
        ("cand", "CANDIDATE ACTIONS\nRaw | Zstd-3 | LZ4 | zlib | delta+Zstd", 505, 335, 480, 92,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;fontSize=17;"),
        ("cost", "END-TO-END COST MODEL\nTready(a) = probe + encode + S(a)/B + decode + rebuild", 505, 465, 480, 120,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;fontStyle=1;fontSize=17;"),
        ("select", "SELECT\nargmin action; Raw remains valid", 650, 640, 190, 115,
         "shape=rhombus;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;fontSize=17;"),
        ("action", "SELECTED ACTION\ncodec + level + bypass flag", 1140, 205, 360, 92,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;fontSize=17;"),
        ("enc", "ENCODE + BUNDLE\npayload + manifest + CRC32C", 1120, 350, 165, 110,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;fontSize=15;"),
        ("raw", "RAW BYPASS\noriginal bytes; no codec", 1315, 350, 165, 110,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF0E8;strokeColor=#D55E00;fontColor=#243447;fontStyle=1;fontSize=15;"),
        ("wire", "CROSS-DOMAIN TRANSFER\npayload + metadata | configured link", 1120, 510, 360, 68,
         "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFF7DE;strokeColor=#E69F00;fontColor=#243447;fontStyle=1;fontSize=16;"),
        ("restore", "DESTINATION-READY RESULT\nverify manifest + CRC32C | decode | rebuild tree", 1120, 650, 360, 100,
         "rounded=1;whiteSpace=wrap;html=1;fillColor=#F9EFF6;strokeColor=#CC79A7;fontColor=#243447;fontStyle=1;fontSize=16;"),
        ("note", "same Tready boundary for Raw, fixed codecs, and CPSS", 555, 810, 900, 34,
         "text;html=1;strokeColor=none;fillColor=none;fontColor=#5D6B78;fontSize=14;fontStyle=2;"),
    ]
    edges = [
        ("e1", "in", "probe"), ("e2", "probe", "feat"), ("e3", "feat", "vec"),
        ("e4", "vec", "cand"), ("e5", "cand", "cost"), ("e6", "cost", "select"),
        ("e7", "select", "action"), ("e8", "action", "enc"), ("e9", "action", "raw"),
        ("e10", "enc", "wire"), ("e11", "raw", "wire"), ("e12", "wire", "restore"),
    ]
    xml = [
        "<mxfile host='app.diagrams.net' modified='2026-10-10T00:00:00.000Z' agent='Codex' version='24.7.17'>",
        "<diagram id='cpss-readable-draft' name='CPSS readable editing draft'>",
        "<mxGraphModel dx='1600' dy='900' grid='1' gridSize='10' page='1' pageWidth='1600' pageHeight='900'><root><mxCell id='0'/><mxCell id='1' parent='0'/>",
    ]
    for cid, label, x, y, w, h, st in cells:
        safe_label = escape(label.replace("\n", "<br>")).replace("'", "&apos;")
        xml.append(f"<mxCell id='{cid}' value='{safe_label}' style='{st}' vertex='1' parent='1'><mxGeometry x='{x}' y='{y}' width='{w}' height='{h}' as='geometry'/></mxCell>")
    for eid, src, dst in edges:
        xml.append(f"<mxCell id='{eid}' value='' style='edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#5D6B78;strokeWidth=2;endArrow=block;endFill=1;' edge='1' parent='1' source='{src}' target='{dst}'><mxGeometry relative='1' as='geometry'/></mxCell>")
    xml.append("</root></mxGraphModel></diagram></mxfile>")
    return "".join(xml)


def drawio_xml(kind: str) -> str:
    """Return a compact editable draw.io source matching the two schematics."""
    if kind == "architecture":
        cells = [
            ("title", "CPSS: costed decision loop around a transfer path", 30, 20, 760, 30, "text;html=1;strokeColor=none;fillColor=none;fontStyle=1;fontSize=18;fontColor=#243447;"),
            ("p1", "I  OBSERVE THE WORKLOAD", 30, 70, 190, 34, "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;"),
            ("p2", "II  ESTIMATE AND CHOOSE", 240, 70, 330, 34, "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;"),
            ("p3", "III  EXECUTE AND RESTORE", 590, 70, 290, 34, "rounded=0;whiteSpace=wrap;html=1;fillColor=#1F3A5F;fontColor=#FFFFFF;strokeColor=#1F3A5F;fontStyle=1;"),
            ("in", "Input bundle<br><font style='font-size:11px'>file tree | raw bytes | metadata</font>", 45, 125, 160, 70, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;"),
            ("probe", "Bounded probe<br><font style='font-size:11px'>4 MiB aligned | max 2 windows</font>", 45, 215, 160, 70, "rounded=1;whiteSpace=wrap;html=1;fillColor=#EEF8FC;strokeColor=#56B4E9;fontColor=#243447;"),
            ("feat", "Feature record<br><font style='font-size:11px'>byte stats | local deltas | type hints</font>", 45, 305, 160, 70, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;"),
            ("vec", "Feature vector x<br><font style='font-size:11px'>byte · delta · float · shape · probe</font>", 260, 125, 290, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F2F8;strokeColor=#0072B2;fontColor=#243447;"),
            ("cand", "Candidate actions<br><font style='font-size:11px'>Raw | Zstd-3 | LZ4 | zlib | delta+Zstd</font>", 260, 215, 290, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;"),
            ("cost", "Cost model<br><font style='font-size:11px'>Tready(a)=probe+encode+S(a)/B+decode+rebuild</font>", 260, 305, 290, 72, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#0072B2;fontColor=#243447;"),
            ("select", "SELECT<br><font style='font-size:11px'>argmin action; Raw remains valid</font>", 365, 410, 100, 65, "shape=rhombus;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;"),
            ("action", "Selected action<br><font style='font-size:11px'>codec + level + bypass flag</font>", 620, 125, 230, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;"),
            ("enc", "Encode + bundle<br><font style='font-size:11px'>payload + manifest + CRC32C</font>", 620, 215, 105, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;"),
            ("raw", "Raw bypass<br><font style='font-size:11px'>original bytes</font>", 745, 215, 105, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF0E8;strokeColor=#D55E00;fontColor=#243447;"),
            ("wire", "CROSS-DOMAIN TRANSFER  |  payload + metadata  |  configured link", 620, 315, 230, 42, "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFF7DE;strokeColor=#E69F00;fontColor=#243447;fontStyle=1;"),
            ("restore", "Destination-ready result<br><font style='font-size:11px'>verify | decode | rebuild tree</font>", 620, 395, 230, 62, "rounded=1;whiteSpace=wrap;html=1;fillColor=#F9EFF6;strokeColor=#CC79A7;fontColor=#243447;"),
        ]
        edges = [("e1", "in", "probe"), ("e2", "probe", "feat"), ("e3", "feat", "vec"), ("e4", "vec", "cand"), ("e5", "cand", "cost"), ("e6", "cost", "select"), ("e7", "select", "action"), ("e8", "action", "enc"), ("e9", "action", "raw"), ("e10", "enc", "wire"), ("e11", "raw", "wire"), ("e12", "wire", "restore")]
    else:
        cells = [
            ("title", "A matched Tready boundary makes the comparison end-to-end", 30, 20, 800, 30, "text;html=1;strokeColor=none;fillColor=none;fontStyle=1;fontSize=18;fontColor=#243447;"),
            ("ph", "PROBE / SCAN", 220, 75, 105, 30, "rounded=0;whiteSpace=wrap;html=1;fillColor=#EEF8FC;strokeColor=#56B4E9;fontColor=#243447;fontStyle=1;"),
            ("en", "FULL-DATA ENCODE", 325, 75, 185, 30, "rounded=0;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;fontStyle=1;"),
            ("tr", "CROSS-DOMAIN TRANSFER", 510, 75, 285, 30, "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFF7DE;strokeColor=#E69F00;fontColor=#243447;fontStyle=1;"),
            ("de", "DECODE / REBUILD", 795, 75, 135, 30, "rounded=0;whiteSpace=wrap;html=1;fillColor=#F9EFF6;strokeColor=#CC79A7;fontColor=#243447;fontStyle=1;"),
            ("raw", "RAW\noriginal bytes  ->  destination ready", 30, 140, 900, 55, "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF0E8;strokeColor=#D55E00;fontColor=#243447;"),
            ("fixed", "FIXED CODEC\nencode  ->  compressed bytes  ->  decode / rebuild", 30, 220, 900, 55, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F2F8;strokeColor=#0072B2;fontColor=#243447;"),
            ("cpss", "CPSS\nbounded probe  ->  conditional encode  ->  selected payload  ->  restore", 30, 300, 900, 55, "rounded=1;whiteSpace=wrap;html=1;fillColor=#E8F5F0;strokeColor=#009E73;fontColor=#243447;"),
            ("note", "Empty phases are bypassed; probe cost is included in CPSS Tready.", 220, 395, 570, 35, "text;html=1;strokeColor=none;fillColor=none;fontColor=#5D6B78;fontSize=12;"),
        ]
        edges = []
    xml = ["<mxfile host='app.diagrams.net' modified='2026-10-10T00:00:00.000Z' agent='Codex' version='24.7.17'>", "<diagram id='cpss-advanced' name='Page-1'><mxGraphModel dx='1200' dy='800' grid='1' gridSize='10' page='1' pageWidth='1100' pageHeight='650'><root><mxCell id='0'/><mxCell id='1' parent='0'/>"]
    for cid, label, x, y, w, h, st in cells:
        safe_label = escape(label.replace("\n", "<br>")).replace("'", "&apos;")
        xml.append(f"<mxCell id='{cid}' value='{safe_label}' style='{st}' vertex='1' parent='1'><mxGeometry x='{x}' y='{y}' width='{w}' height='{h}' as='geometry'/></mxCell>")
    for eid, src, dst in edges:
        xml.append(f"<mxCell id='{eid}' value='' style='edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#5D6B78;endArrow=block;endFill=1;' edge='1' parent='1' source='{src}' target='{dst}'><mxGeometry relative='1' as='geometry'/></mxCell>")
    xml.append("</root></mxGraphModel></diagram></mxfile>")
    return "".join(xml)


def main() -> None:
    style()
    output = Path("docs/dcc_latex/cpss_submission/Figures")
    architecture(output)
    timing_boundary(output)
    (output / "cpss_architecture_color.drawio").write_text(drawio_architecture_editable_xml(), encoding="utf-8")
    (output / "timing_boundary_color.drawio").write_text(drawio_xml("timing"), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Render an uncompressed diagrams.net page to SVG and PNG using CairoSVG."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import cairosvg


class RichText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines = [""]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self.lines.append("")

    def handle_data(self, data: str) -> None:
        self.lines[-1] += data


def style_map(value: str) -> dict[str, str]:
    result = {}
    for item in value.split(";"):
        if "=" in item:
            key, val = item.split("=", 1)
            result[key] = val
        elif item:
            result.setdefault("shape", item)
    return result


def text_lines(value: str) -> list[str]:
    parser = RichText()
    parser.feed(value)
    return [line.strip() for line in parser.lines]


def render(source: Path, png: Path, svg: Path | None = None, scale: float = 2.5) -> None:
    root = ET.parse(source).getroot()
    model = root.find(".//mxGraphModel")
    if model is None:
        raise ValueError("No mxGraphModel found")
    width = float(model.get("pageWidth", "1000"))
    height = float(model.get("pageHeight", "700"))
    cells = {cell.get("id"): cell for cell in root.findall(".//root/mxCell") if cell.get("id")}
    geoms = {}
    for cell_id, cell in cells.items():
        geom = cell.find("mxGeometry")
        if geom is not None:
            geoms[cell_id] = {k: float(geom.get(k, "0")) for k in ("x", "y", "width", "height")}

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<defs><marker id="arrow-gray" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#7B858B"/></marker>'
        '<marker id="arrow-blue" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#2F80C0"/></marker>'
        '<marker id="arrow-cyan" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#007A9E"/></marker>'
        '<marker id="arrow-orange" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#D66A16"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
    ]

    for cell_id in ("bg-decision", "bg-data", "bg-receiver"):
        cell, g = cells[cell_id], geoms[cell_id]
        style = style_map(cell.get("style", ""))
        parts.append(f'<rect x="{g["x"]}" y="{g["y"]}" width="{g["width"]}" height="{g["height"]}" fill="{style.get("fillColor", "none")}"/>')

    boundary = cells.get("domain-boundary")
    if boundary is not None:
        g = geoms["domain-boundary"]
        parts.append(f'<line x1="{g["x"]}" y1="{g["y"]}" x2="{g["x"]}" y2="{g["y"] + g["height"]}" stroke="#D9DEE1" stroke-width="2" stroke-dasharray="6 6"/>')

    def center(cell_id: str) -> tuple[float, float]:
        g = geoms[cell_id]
        return g["x"] + g["width"] / 2, g["y"] + g["height"] / 2

    def anchor(cell_id: str, toward: tuple[float, float]) -> tuple[float, float]:
        g = geoms[cell_id]
        cx, cy = center(cell_id)
        dx, dy = toward[0] - cx, toward[1] - cy
        if abs(dx) * g["height"] >= abs(dy) * g["width"]:
            return (g["x"] + g["width"], cy) if dx >= 0 else (g["x"], cy)
        return (cx, g["y"] + g["height"]) if dy >= 0 else (cx, g["y"])

    edge_labels = []
    custom_routes = {
        "e-select-encode": [(400, 442), (425, 442), (425, 417.5), (445, 417.5)],
        "e-select-raw": [(400, 465), (425, 465), (425, 522.5), (445, 522.5)],
        "e-encode-gridflux": [(675, 417.5), (700, 417.5), (700, 440), (720, 440)],
        "e-raw-gridflux": [(675, 522.5), (700, 522.5), (700, 482), (720, 482)],
    }
    edge_label_positions = {"e-select-encode": (415, 400), "e-select-raw": (415, 535)}
    for cell_id, cell in cells.items():
        if cell.get("edge") != "1":
            continue
        style = style_map(cell.get("style", ""))
        source_id, target_id = cell.get("source"), cell.get("target")
        if source_id not in geoms or target_id not in geoms:
            continue
        source_center, target_center = center(source_id), center(target_id)
        geom = cell.find("mxGeometry")
        waypoints = []
        if geom is not None:
            points = geom.find("Array[@as='points']")
            if points is not None:
                waypoints = [(float(p.get("x", "0")), float(p.get("y", "0"))) for p in points.findall("mxPoint")]
        first_toward = waypoints[0] if waypoints else target_center
        last_toward = waypoints[-1] if waypoints else source_center
        route = custom_routes.get(cell_id)
        if route is None:
            route = [anchor(source_id, first_toward), *waypoints, anchor(target_id, last_toward)]
        d = "M " + " L ".join(f"{x:g} {y:g}" for x, y in route)
        dash = ' stroke-dasharray="6 6"' if style.get("dashed") == "1" else ""
        color = style.get("strokeColor", "#7B858B")
        marker_color = {"#2F80C0": "blue", "#007A9E": "cyan", "#D66A16": "orange"}.get(color, "gray")
        arrow = f' marker-end="url(#arrow-{marker_color})"' if style.get("endArrow", "none") != "none" else ""
        parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{style.get("strokeWidth", "2")}"{dash}{arrow}/>')
        label = text_lines(cell.get("value", ""))
        if label and label[0]:
            x, y = edge_label_positions.get(cell_id, (sum(p[0] for p in route) / len(route), sum(p[1] for p in route) / len(route) - 8))
            edge_labels.append(f'<text x="{x:g}" y="{y:g}" text-anchor="middle" font-family="Georgia,serif" font-size="{style.get("fontSize", "12")}" fill="{style.get("fontColor", color)}">{escape(label[0])}</text>')

    for cell_id, cell in cells.items():
        if cell.get("vertex") != "1" or cell_id in {"bg-decision", "bg-data", "bg-receiver", "domain-boundary"}:
            continue
        g = geoms[cell_id]
        style = style_map(cell.get("style", ""))
        value = cell.get("value", "")
        lines = text_lines(value)
        shape = style.get("shape", "rounded=1")
        fill = style.get("fillColor", "none")
        stroke = style.get("strokeColor", "none")
        sw = style.get("strokeWidth", "1")
        if shape == "ellipse":
            parts.append(f'<ellipse cx="{g["x"] + g["width"] / 2}" cy="{g["y"] + g["height"] / 2}" rx="{g["width"] / 2}" ry="{g["height"] / 2}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif shape == "rhombus":
            pts = f'{g["x"] + g["width"] / 2},{g["y"]} {g["x"] + g["width"]},{g["y"] + g["height"] / 2} {g["x"] + g["width"] / 2},{g["y"] + g["height"]} {g["x"]},{g["y"] + g["height"] / 2}'
            parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        elif shape == "text":
            pass
        else:
            radius = 10 if style.get("rounded") == "1" else 0
            parts.append(f'<rect x="{g["x"]}" y="{g["y"]}" width="{g["width"]}" height="{g["height"]}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')
        if not lines or not any(lines):
            continue
        fs = float(style.get("fontSize", "16"))
        line_height = fs * 1.18
        first_y = g["y"] + g["height"] / 2 - (len(lines) - 1) * line_height / 2 + fs * 0.34
        color = style.get("fontColor", "#263238")
        align = style.get("align", "center")
        tx = g["x"] + g["width"] / 2 if align == "center" else g["x"]
        anchor_text = "middle" if align == "center" else "start"
        for index, line in enumerate(lines):
            line_color = color if index == 0 else "#66727A"
            weight = "700" if index == 0 and "<b>" in value else "400"
            parts.append(f'<text x="{tx:g}" y="{first_y + index * line_height:g}" text-anchor="{anchor_text}" font-family="Georgia,serif" font-size="{fs:g}" font-weight="{weight}" fill="{line_color}">{escape(line)}</text>')

    parts.extend(edge_labels)
    parts.append("</svg>")
    svg_data = "".join(parts).encode("utf-8")
    if svg is not None:
        svg.write_bytes(svg_data)
    png.parent.mkdir(parents=True, exist_ok=True)
    cairosvg.svg2png(bytestring=svg_data, write_to=str(png), output_width=round(width * scale))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("png", type=Path)
    parser.add_argument("--svg", type=Path)
    parser.add_argument("--scale", type=float, default=2.5)
    args = parser.parse_args()
    render(args.source, args.png, args.svg, args.scale)


if __name__ == "__main__":
    main()

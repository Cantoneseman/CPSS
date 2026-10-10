"""Render the editable timing-boundary diagrams.net source to publication assets."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import cairosvg


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[list[tuple[str, str | None]]] = [[]]
        self.size: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "br":
            self.lines.append([])
        elif tag.lower() == "font":
            self.size = dict(attrs).get("size")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "font":
            self.size = None

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.lines[-1].append((data.strip(), self.size))


def styles(cell: ET.Element) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in cell.get("style", "").split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            parsed[key] = value
        elif item:
            parsed.setdefault("shape", item)
    return parsed


def text_lines(value: str) -> list[list[tuple[str, str | None]]]:
    parser = TextParser()
    parser.feed(value)
    return [line for line in parser.lines if line]


def render(source: Path, png: Path, svg: Path, pdf: Path, scale: float) -> None:
    root = ET.parse(source).getroot()
    model = root.find(".//mxGraphModel")
    if model is None:
        raise ValueError("No mxGraphModel found")
    width = float(model.get("pageWidth", "1400"))
    height = float(model.get("pageHeight", "560"))
    cells = [cell for cell in root.findall(".//root/mxCell") if cell.get("id") not in {"0", "1"}]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}">',
        '<rect width="100%" height="100%" fill="#FFFFFF"/>',
    ]

    def geometry(cell: ET.Element) -> ET.Element | None:
        return cell.find("mxGeometry")

    # Draw the light row guides first so they remain behind the process blocks.
    for cell in cells:
        geom = geometry(cell)
        style = styles(cell)
        if cell.get("vertex") != "1" or style.get("shape") != "line" or geom is None:
            continue
        x, y = float(geom.get("x", "0")), float(geom.get("y", "0"))
        w, h = float(geom.get("width", "0")), float(geom.get("height", "0"))
        out.append(f'<line x1="{x:g}" y1="{y:g}" x2="{x + w:g}" y2="{y + h:g}" stroke="{style.get("strokeColor", "#B3B3B3")}" stroke-width="{style.get("strokeWidth", "1")}"/>')

    # Render boxes and their labels.
    for cell in cells:
        if cell.get("vertex") != "1":
            continue
        geom = geometry(cell)
        if geom is None:
            continue
        style = styles(cell)
        if style.get("shape") == "line":
            continue
        x, y = float(geom.get("x", "0")), float(geom.get("y", "0"))
        w, h = float(geom.get("width", "0")), float(geom.get("height", "0"))
        fill = style.get("fillColor", "none")
        stroke = style.get("strokeColor", "none")
        sw = style.get("strokeWidth", "1")
        if style.get("shape") != "text":
            dash = ' stroke-dasharray="8 5"' if style.get("dashed") == "1" else ""
            out.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        lines = text_lines(cell.get("value", ""))
        if not lines:
            continue
        fs = float(style.get("fontSize", "15"))
        line_h = fs * 1.22
        first_y = y + h / 2 - (len(lines) - 1) * line_h / 2 + fs * 0.34
        align = style.get("align", "center")
        anchor = "start" if align == "left" else "middle"
        tx = x + 2 if align == "left" else x + w / 2
        color = style.get("fontColor", "#111111")
        weight = "700" if "fontStyle=1" in cell.get("style", "") else "400"
        for index, line in enumerate(lines):
            if len(line) == 1 and line[0][0] == "T" and "<sub>ready</sub>" in cell.get("value", ""):
                out.append(f'<text x="{tx:g}" y="{first_y + index * line_h:g}" text-anchor="{anchor}" font-family="Arial,sans-serif" font-size="16" fill="{color}"><tspan font-style="italic">T</tspan><tspan baseline-shift="sub" font-size="12">ready</tspan></text>')
                continue
            parts = []
            for word, smaller in line:
                size = float(smaller) if smaller else fs
                parts.append(f'<tspan font-size="{size:g}">{escape(word)}</tspan>')
            out.append(f'<text x="{tx:g}" y="{first_y + index * line_h:g}" text-anchor="{anchor}" font-family="Arial,sans-serif" font-size="{fs:g}" font-weight="{weight}" fill="{color}">{"".join(parts)}</text>')

    # The three edge cells are open-ended braces marking the matched T_ready interval.
    for cell in cells:
        if cell.get("edge") != "1":
            continue
        geom = geometry(cell)
        if geom is None:
            continue
        points = {p.get("as"): p for p in geom.findall("mxPoint")}
        start, end = points.get("sourcePoint"), points.get("targetPoint")
        if start is None or end is None:
            continue
        x1, y = float(start.get("x", "0")), float(start.get("y", "0"))
        x2 = float(end.get("x", "0"))
        stroke = styles(cell).get("strokeColor", "#111111")
        sw = styles(cell).get("strokeWidth", "2")
        out.append(f'<path d="M{x1:g},{y + 9:g} V{y:g} H{x2:g} V{y + 9:g}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')
        out.append(f'<text x="{(x1 + x2) / 2:g}" y="{y - 8:g}" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#111111"><tspan font-style="italic">T</tspan><tspan baseline-shift="sub" font-size="12">ready</tspan></text>')

    out.append("</svg>")
    data = "".join(out).encode("utf-8")
    svg.parent.mkdir(parents=True, exist_ok=True)
    png.parent.mkdir(parents=True, exist_ok=True)
    pdf.parent.mkdir(parents=True, exist_ok=True)
    svg.write_bytes(data)
    cairosvg.svg2png(bytestring=data, write_to=str(png), output_width=round(width * scale))
    cairosvg.svg2pdf(bytestring=data, write_to=str(pdf))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("png", type=Path)
    parser.add_argument("--svg", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()
    render(args.source, args.png, args.svg, args.pdf, args.scale)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from .range_downloader import DEFAULT_CHUNK_BYTES, DownloadPlan, assemble_from_ranges
except ImportError:
    from range_downloader import DEFAULT_CHUNK_BYTES, DownloadPlan, assemble_from_ranges


ROOT = Path(r"E:\Dataset\AI_Dataset")
DEFAULT_URLS_FILE = ROOT / "download_logs" / "redpajama_urls.txt"
STATUS_JSONL = ROOT / "download_logs" / "download_status.jsonl"
GIB = 1024**3


@dataclass(frozen=True)
class Candidate:
    url: str
    bytes_expected: int


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log_event(slug: str, status: str, message: str, **extra: object) -> None:
    STATUS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": now(), "slug": slug, "status": status, "message": message, **extra}
    with STATUS_JSONL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        if path.name.endswith((".part", ".assembling")):
            return 0
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if item.name.endswith((".part", ".assembling")):
            continue
        if any(parent.name.startswith(".") and parent.name.endswith(".parts") for parent in item.parents):
            continue
        total += item.stat().st_size
    return total


def urls_for_component(urls: Iterable[str], component: str) -> list[str]:
    needle = f"/{component.strip('/')}/"
    return [url for url in urls if needle in urllib.parse.urlparse(url).path]


def filename_for_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"cannot derive component and filename from {url}")
    component = parts[-2]
    leaf = parts[-1]
    safe_leaf = re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.unquote(leaf))
    return f"redpajama_{component}_{safe_leaf}"


def parse_head(text: str) -> tuple[int | None, int | None]:
    status = None
    length = None
    for raw_line in text.replace("\r", "").splitlines():
        line = raw_line.strip()
        match = re.match(r"HTTP/\S+\s+(\d+)", line)
        if match:
            status = int(match.group(1))
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
    return status, length


def head_candidate(url: str) -> Candidate | None:
    cmd = [
        "curl.exe",
        "-I",
        "-L",
        "--ssl-no-revoke",
        "--connect-timeout",
        "30",
        "--max-time",
        "120",
        url,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True, check=False)
    status, length = parse_head(result.stdout + result.stderr)
    if result.returncode != 0 or status is None or status >= 400 or length is None:
        return None
    return Candidate(url, length)


def plan_downloads(candidates: Iterable[Candidate], dest_dir: Path, target_bytes: int) -> list[Candidate]:
    current = size_bytes(dest_dir)
    planned: list[Candidate] = []
    for candidate in candidates:
        if current >= target_bytes:
            break
        output_path = dest_dir / filename_for_url(candidate.url)
        if output_path.exists() and output_path.stat().st_size == candidate.bytes_expected:
            continue
        planned.append(candidate)
        current += candidate.bytes_expected
    return planned


def read_urls(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def discover_candidates(urls: Iterable[str], *, limit_probe: int | None = None) -> list[Candidate]:
    candidates: list[Candidate] = []
    for index, url in enumerate(urls):
        if limit_probe is not None and index >= limit_probe:
            break
        candidate = head_candidate(url)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download bounded Together RedPajama samples with HTTP Range requests.")
    parser.add_argument("--urls-file", type=Path, default=DEFAULT_URLS_FILE)
    parser.add_argument("--component", required=True, help="Path component to select, for example arxiv or github.")
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--slug", default="", help="Dataset slug to use in download_status.jsonl.")
    parser.add_argument("--target-gib", type=float, required=True)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--source-chunk-bytes", type=int, default=0)
    parser.add_argument("--max-files", type=int, default=0, help="Optional cap on files to download after planning.")
    parser.add_argument("--probe-files", type=int, default=0, help="Optional cap on URLs to HEAD while discovering sizes.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-parts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    slug = args.slug or f"redpajama_{args.component}"
    target_bytes = int(args.target_gib * GIB)
    urls = urls_for_component(read_urls(args.urls_file), args.component)
    if not urls:
        log_event(slug, "blocked", f"no URLs matched component {args.component}", urls_file=str(args.urls_file))
        return 1

    log_event(slug, "discover_start", f"probing {len(urls)} URLs", component=args.component)
    candidates = discover_candidates(urls, limit_probe=args.probe_files or None)
    planned = plan_downloads(candidates, args.dest, target_bytes)
    if args.max_files:
        planned = planned[: args.max_files]
    log_event(
        slug,
        "planned",
        f"{len(planned)} files selected",
        current_gib=round(size_bytes(args.dest) / GIB, 3),
        target_gib=args.target_gib,
        candidate_count=len(candidates),
    )
    if args.dry_run:
        for candidate in planned:
            print(f"{candidate.bytes_expected}\t{candidate.url}", flush=True)
        return 0

    args.dest.mkdir(parents=True, exist_ok=True)
    for candidate in planned:
        output_path = args.dest / filename_for_url(candidate.url)
        log_event(slug, "download_start", candidate.url, output=str(output_path), expected_bytes=candidate.bytes_expected)
        result = assemble_from_ranges(
            DownloadPlan(candidate.url, output_path, candidate.bytes_expected, args.chunk_bytes),
            source_chunk_bytes=args.source_chunk_bytes or None,
            keep_parts=args.keep_parts,
        )
        log_event(
            slug,
            result.status,
            output_path.name,
            size_bytes=result.bytes_written,
            downloaded_parts=result.parts_downloaded,
            reused_parts=result.parts_reused,
            total_gib=round(size_bytes(args.dest) / GIB, 3),
        )
        if size_bytes(args.dest) >= target_bytes:
            break
    log_event(slug, "summary", "Together sample status", total_gib=round(size_bytes(args.dest) / GIB, 3))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

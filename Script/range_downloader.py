from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_CHUNK_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class DownloadPlan:
    url: str
    output_path: Path
    expected_bytes: int
    chunk_bytes: int = DEFAULT_CHUNK_BYTES


@dataclass(frozen=True)
class DownloadResult:
    status: str
    path: Path
    bytes_written: int
    parts_downloaded: int
    parts_reused: int


PartFetcher = Callable[[int, int, Path], None]


def _part_dir_for(output_path: Path) -> Path:
    return output_path.parent / f".{output_path.name}.parts"


def _spans(expected_bytes: int, chunk_bytes: int) -> list[tuple[int, int, int]]:
    if expected_bytes < 0:
        raise ValueError("expected_bytes must be non-negative")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    spans = []
    for index, start in enumerate(range(0, expected_bytes, chunk_bytes)):
        end = min(expected_bytes - 1, start + chunk_bytes - 1)
        spans.append((index, start, end))
    return spans


def split_existing_parts(
    part_dir: Path,
    *,
    source_chunk_bytes: int,
    target_chunk_bytes: int,
    expected_bytes: int,
) -> int:
    if source_chunk_bytes <= 0:
        raise ValueError("source_chunk_bytes must be positive")
    if target_chunk_bytes <= 0:
        raise ValueError("target_chunk_bytes must be positive")
    if source_chunk_bytes == target_chunk_bytes or not part_dir.exists():
        return 0

    part_pattern = re.compile(r"part_(\d+)$")
    source_parts: list[tuple[int, Path]] = []
    for path in part_dir.iterdir():
        if not path.is_file():
            continue
        match = part_pattern.fullmatch(path.name)
        if match:
            source_parts.append((int(match.group(1)), path))

    tmp_dir = part_dir / ".split_tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)
    converted = 0
    try:
        for source_index, source_path in sorted(source_parts):
            source_start = source_index * source_chunk_bytes
            if source_start >= expected_bytes:
                continue
            source_size = source_path.stat().st_size
            with source_path.open("rb") as source:
                offset = 0
                while offset + target_chunk_bytes <= source_size:
                    target_start = source_start + offset
                    target_end = target_start + target_chunk_bytes - 1
                    if target_end >= expected_bytes:
                        break
                    if target_start % target_chunk_bytes != 0:
                        break
                    target_index = target_start // target_chunk_bytes
                    target_path = tmp_dir / f"part_{target_index:06d}"
                    source.seek(offset)
                    target_path.write_bytes(source.read(target_chunk_bytes))
                    converted += 1
                    offset += target_chunk_bytes

        for _, source_path in source_parts:
            source_path.unlink()
        for target_path in tmp_dir.iterdir():
            final_path = part_dir / target_path.name
            if final_path.exists():
                final_path.unlink()
            target_path.replace(final_path)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
    return converted


def fetch_part_with_curl(url: str, start: int, end: int, part_path: Path) -> None:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required for range downloads")
    part_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        curl,
        "-L",
        "--fail",
        "--ssl-no-revoke",
        "--retry",
        "12",
        "--retry-all-errors",
        "--retry-delay",
        "10",
        "--connect-timeout",
        "30",
        "--max-time",
        "1800",
        "--speed-limit",
        "8192",
        "--speed-time",
        "300",
        "--range",
        f"{start}-{end}",
        "--output",
        str(part_path),
        url,
    ]
    result = subprocess.run(cmd, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"curl failed with exit code {result.returncode}")


def assemble_from_ranges(
    plan: DownloadPlan,
    *,
    fetcher: PartFetcher | None = None,
    part_dir: Path | None = None,
    source_chunk_bytes: int | None = None,
    keep_parts: bool = False,
) -> DownloadResult:
    output_path = plan.output_path
    if output_path.exists() and output_path.stat().st_size == plan.expected_bytes:
        return DownloadResult("already_complete", output_path, plan.expected_bytes, 0, 0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fetcher = fetcher or (lambda start, end, path: fetch_part_with_curl(plan.url, start, end, path))
    part_dir = part_dir or _part_dir_for(output_path)
    part_dir.mkdir(parents=True, exist_ok=True)
    if source_chunk_bytes is not None and source_chunk_bytes != plan.chunk_bytes:
        split_existing_parts(
            part_dir,
            source_chunk_bytes=source_chunk_bytes,
            target_chunk_bytes=plan.chunk_bytes,
            expected_bytes=plan.expected_bytes,
        )

    parts_downloaded = 0
    parts_reused = 0
    part_paths: list[Path] = []
    for index, start, end in _spans(plan.expected_bytes, plan.chunk_bytes):
        part_path = part_dir / f"part_{index:06d}"
        part_paths.append(part_path)
        wanted = end - start + 1
        if part_path.exists() and part_path.stat().st_size == wanted:
            parts_reused += 1
            continue
        if part_path.exists():
            part_path.unlink()
        fetcher(start, end, part_path)
        got = part_path.stat().st_size if part_path.exists() else 0
        if got != wanted:
            raise RuntimeError(f"{part_path} has wrong size: got {got}, expected {wanted}")
        parts_downloaded += 1

    tmp_path = output_path.with_name(output_path.name + ".assembling")
    if tmp_path.exists():
        tmp_path.unlink()
    with tmp_path.open("wb") as merged:
        for part_path in part_paths:
            with part_path.open("rb") as part:
                shutil.copyfileobj(part, merged, length=8 * 1024 * 1024)
    written = tmp_path.stat().st_size
    if written != plan.expected_bytes:
        raise RuntimeError(f"{tmp_path} has wrong size: got {written}, expected {plan.expected_bytes}")
    tmp_path.replace(output_path)

    if not keep_parts:
        for part_path in part_paths:
            part_path.unlink(missing_ok=True)
        try:
            part_dir.rmdir()
        except OSError:
            pass

    return DownloadResult("downloaded", output_path, written, parts_downloaded, parts_reused)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download one file through fixed-size HTTP Range requests.")
    parser.add_argument("url")
    parser.add_argument("output_path", type=Path)
    parser.add_argument("--expected-bytes", type=int, required=True)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--source-chunk-bytes", type=int, default=0)
    parser.add_argument("--keep-parts", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    plan = DownloadPlan(
        url=args.url,
        output_path=args.output_path,
        expected_bytes=args.expected_bytes,
        chunk_bytes=args.chunk_bytes,
    )
    result = assemble_from_ranges(
        plan,
        source_chunk_bytes=args.source_chunk_bytes or None,
        keep_parts=args.keep_parts,
    )
    print(
        f"{result.status}\t{result.bytes_written}\t"
        f"downloaded={result.parts_downloaded}\treused={result.parts_reused}\t{result.path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

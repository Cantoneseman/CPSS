#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


GiB = 1024**3


@dataclass(frozen=True)
class Dataset:
    slug: str
    name: str
    kind: str
    urls: tuple[str, ...] = ()
    target_uncompressed_gib: float | None = None
    notes: str = ""


DATASETS: tuple[Dataset, ...] = (
    Dataset(
        "hurricane_isabel",
        "Hurricane-ISABEL",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/Hurricane-ISABEL/"
            "SDRBENCH-Hurricane-ISABEL-100x500x500.tar.gz",
        ),
        notes="SDRBench Hurricane-ISABEL official tar.gz.",
    ),
    Dataset(
        "miranda",
        "Miranda",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/Miranda/"
            "SDRBENCH-Miranda-256x384x384.tar.gz",
        ),
        notes="SDRBench Miranda official tar.gz.",
    ),
    Dataset(
        "cesm_atm_dataset1",
        "CESM-ATM Dataset1",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/CESM-ATM/"
            "SDRBENCH-CESM-ATM-1800x3600.tar.gz",
        ),
        notes="SDRBench CESM-ATM Dataset1 official tar.gz.",
    ),
    Dataset(
        "hacc_medium",
        "HACC medium-size",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXASKY/HACC/"
            "EXASKY-HACC-data-medium-size.tar.gz",
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXASKY/HACC/"
            "EXASKY-HACC-data-big-size.tar.gz",
        ),
        notes="HACC medium primary, big-size fallback if medium is unavailable.",
    ),
    Dataset(
        "nyx",
        "NYX",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXASKY/NYX/"
            "SDRBENCH-EXASKY-NYX-512x512x512.tar.gz",
        ),
        notes="SDRBench EXASKY NYX official tar.gz.",
    ),
    Dataset(
        "scale_letkf",
        "SCALE-LETKF",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/SCALE_LETKF/"
            "SDRBENCH-SCALE-98x1200x1200.tar.gz",
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/SCALE-LETKF/"
            "SDRBENCH-SCALE-98x1200x1200.tar.gz",
        ),
        notes="SCALE_LETKF primary, SCALE-LETKF fallback.",
    ),
    Dataset(
        "exafel",
        "EXAFEL",
        "direct",
        (
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXAFEL/SDRBENCH-EXAFEL.tar.gz",
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXAFEL/"
            "SDRBENCH-EXAFEL-10x32x185x388.tar.gz",
            "https://g-8d6b0.fd635.8443.data.globus.org/ds131.2/"
            "Data-Reduction-Repo/raw-data/EXASKY/EXAFEL/SDRBENCH-EXAFEL.tar.gz",
        ),
        notes="EXAFEL official candidates; currently the shaped tar.gz is the reachable source.",
    ),
    Dataset(
        "rcsb_mmcif",
        "RCSB-mmCIF",
        "rcsb_mmcif",
        ("https://files.wwpdb.org/pub/pdb/data/structures/divided/mmCIF/",),
        target_uncompressed_gib=12.0,
        notes="Deterministic subset of divided mmCIF .cif.gz files; stop near 12GiB uncompressed estimate.",
    ),
    Dataset(
        "alphafold_mmcif_subset",
        "AlphaFold DB mmCIF subset",
        "direct",
        ("https://ftp.ebi.ac.uk/pub/databases/alphafold/latest/UP000005640_9606_HUMAN_v6.tar",),
        notes="AlphaFold Human v6 tar.",
    ),
    Dataset(
        "ncbi_human_grch38_fasta",
        "NCBI Human GRCh38 FASTA",
        "direct",
        (
            "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/"
            "GCF_000001405.40_GRCh38.p14/"
            "GCF_000001405.40_GRCh38.p14_genomic.fna.gz",
        ),
        notes="NCBI GRCh38.p14 genomic FASTA gzip.",
    ),
)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)


def parse_head(text: str) -> dict[str, Any]:
    status = None
    length = None
    content_type = ""
    for raw_line in text.replace("\r", "").splitlines():
        line = raw_line.strip()
        match = re.match(r"HTTP/\S+\s+(\d+)", line, re.I)
        if match:
            status = int(match.group(1))
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        if line.lower().startswith("content-type:"):
            content_type = line.split(":", 1)[1].strip()
    return {"status": status, "content_length": length, "content_type": content_type}


def head_url(url: str) -> dict[str, Any]:
    cp = run(
        [
            "curl.exe",
            "-I",
            "-L",
            "--ssl-no-revoke",
            "--connect-timeout",
            "30",
            "--max-time",
            "90",
            url,
        ],
        timeout=120,
    )
    meta = parse_head(cp.stdout + cp.stderr)
    meta.update({"returncode": cp.returncode, "url": url})
    return meta


def filename_from_url(url: str) -> str:
    name = Path(urllib.parse.urlparse(url).path).name
    return name or "download.bin"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_url(url: str, out: Path, expected_size: int | None) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    if expected_size and out.exists() and out.stat().st_size == expected_size:
        return {"status": "already_complete", "path": str(out), "bytes": expected_size}
    if expected_size and expected_size >= 512 * 1024 * 1024:
        return download_url_parts(url, out, expected_size)
    tmp = out
    cmd = [
        "curl.exe",
        "-L",
        "--fail",
        "--ssl-no-revoke",
        "--retry",
        "10",
        "--retry-all-errors",
        "--retry-delay",
        "15",
        "--connect-timeout",
        "30",
        "--speed-limit",
        "8192",
        "--speed-time",
        "600",
        "--continue-at",
        "-",
        "--output",
        str(tmp),
        url,
    ]
    log(f"download start: {url} -> {out}")
    cp = subprocess.run(cmd, text=True, check=False)
    size = out.stat().st_size if out.exists() else 0
    status = "downloaded" if cp.returncode == 0 else "failed"
    if expected_size and size != expected_size:
        status = "size_mismatch"
    return {"status": status, "returncode": cp.returncode, "path": str(out), "bytes": size, "expected_bytes": expected_size}


def download_part(url: str, start: int, end: int, part_path: Path) -> dict[str, Any]:
    wanted = end - start + 1
    if part_path.exists() and part_path.stat().st_size == wanted:
        return {"status": "already_complete", "part": str(part_path), "bytes": wanted}
    if part_path.exists():
        part_path.unlink()
    cmd = [
        "curl.exe",
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
    log(f"part download start: {part_path.name} range={start}-{end}")
    cp = subprocess.run(cmd, text=True, check=False)
    got = part_path.stat().st_size if part_path.exists() else 0
    if cp.returncode != 0 or got != wanted:
        return {
            "status": "part_failed",
            "returncode": cp.returncode,
            "part": str(part_path),
            "part_bytes": got,
            "part_expected_bytes": wanted,
        }
    return {"status": "downloaded", "part": str(part_path), "bytes": got}


def download_url_parts(url: str, out: Path, expected_size: int, parts: int = 8, concurrency: int = 4) -> dict[str, Any]:
    out.parent.mkdir(parents=True, exist_ok=True)
    part_dir = out.parent / f".{out.name}.parts"
    part_dir.mkdir(parents=True, exist_ok=True)
    spans: list[tuple[int, int, Path]] = []
    part_size = (expected_size + parts - 1) // parts
    for index in range(parts):
        start = index * part_size
        end = min(expected_size - 1, (index + 1) * part_size - 1)
        if start > end:
            continue
        spans.append((start, end, part_dir / f"part_{index:03d}"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(download_part, url, start, end, part_path): (start, end, part_path)
            for start, end, part_path in spans
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result.get("status") not in {"downloaded", "already_complete"}:
                start, end, part_path = futures[future]
                got = part_path.stat().st_size if part_path.exists() else 0
                wanted = end - start + 1
                return {
                    "status": "part_failed",
                    "path": str(out),
                    "part": str(part_path),
                    "part_bytes": got,
                    "part_expected_bytes": wanted,
                    "expected_bytes": expected_size,
                    "part_result": result,
                }
            log(f"part complete: {Path(result['part']).name} status={result['status']} bytes={result['bytes']}")

    for start, end, part_path in spans:
        wanted = end - start + 1
        got = part_path.stat().st_size if part_path.exists() else 0
        if got != wanted:
            return {
                "status": "part_failed",
                "path": str(out),
                "part": str(part_path),
                "part_bytes": got,
                "part_expected_bytes": wanted,
                "expected_bytes": expected_size,
            }

    tmp_out = out.with_suffix(out.suffix + ".assembling")
    with tmp_out.open("wb") as merged:
        for _, _, part_path in spans:
            with part_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                    merged.write(chunk)
    if tmp_out.stat().st_size != expected_size:
        return {
            "status": "size_mismatch",
            "path": str(out),
            "bytes": tmp_out.stat().st_size,
            "expected_bytes": expected_size,
        }
    if out.exists():
        out.unlink()
    tmp_out.replace(out)
    for _, _, part_path in spans:
        part_path.unlink(missing_ok=True)
    try:
        part_dir.rmdir()
    except OSError:
        pass
    return {"status": "downloaded", "path": str(out), "bytes": expected_size, "expected_bytes": expected_size}


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "science-compressor-dataset-staging/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_hrefs(html: str) -> list[str]:
    return [urllib.parse.unquote(item) for item in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)]


def gzip_isize(path: Path) -> int:
    if not path.exists() or path.stat().st_size < 4:
        return 0
    with path.open("rb") as handle:
        handle.seek(-4, os.SEEK_END)
        return struct.unpack("<I", handle.read(4))[0]


def gzip_uncompressed_size_verified(path: Path) -> int | None:
    try:
        total = 0
        with gzip.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
                total += len(chunk)
        return total
    except OSError:
        return None


def ensure_rcsb_file(url: str, out: Path) -> dict[str, Any]:
    uncompressed = gzip_uncompressed_size_verified(out) if out.exists() else None
    if out.exists() and uncompressed is None:
        out.unlink()
    if not out.exists():
        result = download_url(url, out, None)
        if result["status"] not in {"downloaded", "already_complete"}:
            return {"url": url, **result}
        uncompressed = gzip_uncompressed_size_verified(out)
        if uncompressed is None:
            out.unlink(missing_ok=True)
            return {"url": url, "path": str(out), "status": "invalid_gzip"}
    return {
        "url": url,
        "path": str(out),
        "status": "downloaded",
        "compressed_bytes": out.stat().st_size,
        "gzip_footer_uncompressed_bytes": int(uncompressed),
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def direct_dataset(root: Path, dataset: Dataset, plan: bool) -> dict[str, Any]:
    chosen = None
    heads = []
    for url in dataset.urls:
        meta = head_url(url)
        heads.append(meta)
        if meta.get("status") and 200 <= int(meta["status"]) < 300:
            chosen = meta
            break
    record: dict[str, Any] = {
        "slug": dataset.slug,
        "name": dataset.name,
        "kind": dataset.kind,
        "notes": dataset.notes,
        "heads": heads,
        "selected_url": chosen["url"] if chosen else None,
    }
    if not chosen:
        record["status"] = "no_reachable_source"
        return record
    out = root / dataset.slug / "source" / filename_from_url(chosen["url"])
    record["local_path"] = str(out)
    record["expected_bytes"] = chosen.get("content_length")
    if plan:
        record["status"] = "planned"
        return record
    result = download_url(chosen["url"], out, chosen.get("content_length"))
    record.update(result)
    if record["status"] in {"downloaded", "already_complete"} and out.exists():
        record["sha256"] = sha256_file(out)
    return record


def rcsb_dataset(root: Path, dataset: Dataset, plan: bool) -> dict[str, Any]:
    base = dataset.urls[0]
    target = int((dataset.target_uncompressed_gib or 12.0) * GiB)
    record: dict[str, Any] = {
        "slug": dataset.slug,
        "name": dataset.name,
        "kind": dataset.kind,
        "base_url": base,
        "target_uncompressed_bytes": target,
        "notes": dataset.notes,
        "files": [],
    }
    root_html = fetch_text(base)
    subdirs = sorted(
        href.rstrip("/")
        for href in extract_hrefs(root_html)
        if re.fullmatch(r"[0-9a-z]{2}/?", href, flags=re.I)
    )
    record["subdir_count"] = len(subdirs)
    if plan:
        record["status"] = "planned"
        record["first_subdirs"] = subdirs[:10]
        return record

    total_uncompressed = 0
    total_compressed = 0
    files_downloaded = 0
    source_dir = root / dataset.slug / "source" / "divided"
    for subdir in subdirs:
        sub_url = urllib.parse.urljoin(base, f"{subdir}/")
        try:
            html = fetch_text(sub_url)
        except Exception as exc:
            log(f"RCSB listing failed {sub_url}: {exc}")
            continue
        names = sorted(href for href in extract_hrefs(html) if href.endswith(".cif.gz"))
        jobs = [
            (urllib.parse.urljoin(sub_url, name), source_dir / subdir / Path(name).name)
            for name in names
        ]
        for offset in range(0, len(jobs), 16):
            if total_uncompressed >= target:
                break
            batch = jobs[offset: offset + 16]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(ensure_rcsb_file, url, out) for url, out in batch]
                for future in concurrent.futures.as_completed(futures):
                    item = future.result()
                    record["files"].append(item)
                    if item.get("status") not in {"downloaded", "already_complete"}:
                        continue
                    compressed = int(item.get("compressed_bytes") or 0)
                    uncompressed = int(item.get("gzip_footer_uncompressed_bytes") or 0)
                    total_compressed += compressed
                    total_uncompressed += uncompressed
                    files_downloaded += 1
                    if total_uncompressed >= target:
                        break
            if total_uncompressed >= target:
                record.update(
                    {
                        "status": "downloaded",
                        "files_downloaded": files_downloaded,
                        "compressed_bytes": total_compressed,
                        "estimated_uncompressed_bytes": total_uncompressed,
                    }
                )
                return record
    record.update(
        {
            "status": "source_exhausted",
            "files_downloaded": files_downloaded,
            "compressed_bytes": total_compressed,
            "estimated_uncompressed_bytes": total_uncompressed,
        }
    )
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download fixed datasets to OSS staging directory.")
    parser.add_argument("--root", type=Path, default=Path(r"E:\Dataset"))
    parser.add_argument("--only", action="append", help="Dataset slug to download; can be repeated.")
    parser.add_argument("--plan", action="store_true", help="Only verify source metadata; do not download payloads.")
    parser.add_argument("--skip-rcsb", action="store_true", help="Skip RCSB directory subset.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    selected = set(args.only or [])
    lock = root / "_download_fixed_datasets.lock"
    if lock.exists() and not args.plan:
        raise SystemExit(f"download lock exists: {lock}")
    if not args.plan:
        lock.write_text(f"pid={os.getpid()}\nstarted_at={now()}\n", encoding="utf-8")
    manifest_path = root / "_manifest" / "download_manifest_latest.json"
    records: list[dict[str, Any]] = []
    try:
        for dataset in DATASETS:
            if selected and dataset.slug not in selected:
                continue
            if args.skip_rcsb and dataset.kind == "rcsb_mmcif":
                continue
            log(f"dataset begin: {dataset.slug} ({dataset.name})")
            if dataset.kind == "direct":
                record = direct_dataset(root, dataset, args.plan)
            elif dataset.kind == "rcsb_mmcif":
                record = rcsb_dataset(root, dataset, args.plan)
            else:
                record = {"slug": dataset.slug, "status": "unsupported_kind"}
            record["finished_at"] = now()
            records.append(record)
            write_json(manifest_path, {"root": str(root), "plan": args.plan, "records": records})
            log(f"dataset end: {dataset.slug} status={record.get('status')}")
        return 0
    finally:
        if lock.exists() and not args.plan:
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())

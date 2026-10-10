from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard as zstd


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_output_path(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or ".." in normalized.split("/") or not posix.parts:
        raise ValueError(f"unsafe archive member: {member_name}")
    candidate = root.joinpath(*posix.parts)
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise ValueError(f"unsafe archive member: {member_name}")
    return candidate


def extract_dataset_archive(
    archive_path: Path,
    output_root: Path,
    record: dict[str, Any],
) -> Path:
    expected_archive_sha256 = str(record["archive_sha256"])
    actual_archive_sha256 = file_sha256(archive_path)
    if actual_archive_sha256 != expected_archive_sha256:
        raise ValueError(
            f"archive SHA-256 mismatch for {record['slug']}: "
            f"expected {expected_archive_sha256}, got {actual_archive_sha256}"
        )

    slug = str(record["slug"])
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / slug
    if destination.exists():
        raise FileExistsError(f"dataset output already exists: {destination}")
    temporary_root = output_root / f".{slug}.partial-{uuid.uuid4().hex}"
    temporary_root.mkdir(parents=True)
    try:
        with archive_path.open("rb") as source:
            with zstd.ZstdDecompressor().stream_reader(source) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as archive:
                    for member in archive:
                        target = safe_output_path(temporary_root, member.name)
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        if not member.isfile():
                            raise ValueError(f"unsupported archive member type: {member.name}")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        extracted = archive.extractfile(member)
                        if extracted is None:
                            raise ValueError(f"cannot read archive member: {member.name}")
                        partial = target.with_name(target.name + ".partial")
                        with partial.open("wb") as output:
                            shutil.copyfileobj(extracted, output, length=8 * 1024 * 1024)
                        partial.replace(target)

        staged_dataset = temporary_root / slug
        if not staged_dataset.is_dir():
            raise ValueError(f"archive did not contain expected dataset root: {slug}")
        for member in record["members"]:
            relative = PurePosixPath(str(member["path"]))
            payload = temporary_root.joinpath(*relative.parts)
            if not payload.is_file():
                raise FileNotFoundError(payload)
            if payload.stat().st_size != int(member["bytes"]):
                raise ValueError(f"payload size mismatch: {payload}")
            if file_sha256(payload) != str(member["sha256"]):
                raise ValueError(f"payload SHA-256 mismatch: {payload}")
        staged_dataset.replace(destination)
        return destination
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)


def parse_args() -> argparse.Namespace:
    default_bundle = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Verify and extract DCC full-v1 datasets.")
    parser.add_argument("--bundle-root", type=Path, default=default_bundle)
    parser.add_argument("--output", type=Path, default=Path("datasets"))
    parser.add_argument("--select", nargs="*", help="Dataset slugs to extract; omit to extract all.")
    parser.add_argument("--list", action="store_true", help="List manifest datasets without extracting.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle_root = args.bundle_root.resolve()
    manifest = json.loads((bundle_root / "metadata" / "datasets.json").read_text(encoding="utf-8"))
    records = manifest["datasets"]
    if args.list:
        for record in records:
            shape = "x".join(str(item) for item in record.get("shape") or []) or "byte-stream"
            print(f"{record['slug']}\t{record['domain']}\t{record.get('dtype') or '-'}\t{shape}")
        return 0

    selected = set(args.select or [])
    if selected:
        unknown = selected - {str(record["slug"]) for record in records}
        if unknown:
            raise SystemExit(f"unknown dataset slug(s): {', '.join(sorted(unknown))}")
        records = [record for record in records if record["slug"] in selected]
    for record in records:
        archive_path = bundle_root / Path(str(record["archive"]))
        destination = extract_dataset_archive(archive_path, args.output.resolve(), record)
        print(f"verified and extracted: {record['slug']} -> {destination}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

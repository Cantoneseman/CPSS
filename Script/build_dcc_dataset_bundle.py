from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import shutil
import tarfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

import zstandard as zstd


SCHEMA_VERSION = 1
DTYPE_BYTES = {"float32": 4, "float64": 8, "uint16": 2}
DTYPE_SUFFIX = {"float32": ".f32", "float64": ".d64", "uint16": ".u16"}


@dataclass(frozen=True)
class DatasetSpec:
    slug: str
    display_name: str
    domain: str
    category: str
    source_path: Path
    source_url: str
    dtype: str | None = None
    shape: tuple[int, ...] | None = None
    endian: str | None = None


class HashingReader:
    def __init__(self, source: BinaryIO) -> None:
        self.source = source
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        payload = self.source.read(size)
        self.digest.update(payload)
        self.bytes_read += len(payload)
        return payload

    def hexdigest(self) -> str:
        return self.digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member_basename(name: str) -> str:
    normalized = name.replace("\\", "/")
    segments = normalized.split("/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in segments
        or (segments and re.fullmatch(r"[A-Za-z]:", segments[0]))
        or not path.name
    ):
        raise ValueError(f"unsafe archive member: {name}")
    return path.name


def expected_field_bytes(dtype: str, shape: tuple[int, ...]) -> int:
    if dtype not in DTYPE_BYTES:
        raise ValueError(f"unsupported dtype: {dtype}")
    if not shape or any(dimension <= 0 for dimension in shape):
        raise ValueError(f"invalid shape: {shape}")
    return math.prod(shape) * DTYPE_BYTES[dtype]


@contextmanager
def open_source_tar(path: Path) -> Iterator[tarfile.TarFile]:
    if path.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, mode="r|gz") as archive:
            yield archive
        return
    if path.name.endswith(".tar.zst"):
        with path.open("rb") as source:
            with zstd.ZstdDecompressor().stream_reader(source) as reader:
                with tarfile.open(fileobj=reader, mode="r|") as archive:
                    yield archive
        return
    raise ValueError(f"unsupported source archive: {path}")


def normalized_tar_info(path: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(path)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def _embedded_metadata(spec: DatasetSpec, source_sha256: str, members: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "slug": spec.slug,
        "display_name": spec.display_name,
        "domain": spec.domain,
        "category": spec.category,
        "dtype": spec.dtype,
        "shape": list(spec.shape) if spec.shape else None,
        "endian": spec.endian,
        "source_archive": spec.source_path.name,
        "source_archive_sha256": source_sha256,
        "source_url": spec.source_url,
        "members": members,
    }


def build_dataset_archive(
    spec: DatasetSpec,
    output_path: Path,
    *,
    compression_level: int = 3,
) -> dict[str, object]:
    if not spec.source_path.is_file():
        raise FileNotFoundError(spec.source_path)
    if spec.dtype is not None and (spec.shape is None or spec.endian not in {"little", "big"}):
        raise ValueError(f"typed dataset {spec.slug} requires shape and endian")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial = output_path.with_name(output_path.name + ".partial")
    partial.unlink(missing_ok=True)
    source_sha256 = file_sha256(spec.source_path)
    members: list[dict[str, object]] = []
    basenames: set[str] = set()
    raw_bytes = 0

    try:
        with partial.open("wb") as raw_output:
            compressor = zstd.ZstdCompressor(level=compression_level, threads=-1, write_checksum=True)
            with compressor.stream_writer(raw_output, closefd=False) as compressed_output:
                with tarfile.open(fileobj=compressed_output, mode="w|") as output_tar:
                    with open_source_tar(spec.source_path) as source_tar:
                        for source_member in source_tar:
                            if not source_member.isfile():
                                continue
                            basename = safe_member_basename(source_member.name)

                            if spec.dtype is not None:
                                suffix = DTYPE_SUFFIX[spec.dtype]
                                if not basename.lower().endswith(suffix):
                                    continue
                                wanted = expected_field_bytes(spec.dtype, spec.shape or ())
                                if source_member.size != wanted:
                                    raise ValueError(
                                        f"{source_member.name} has {source_member.size} bytes; expected {wanted} bytes"
                                    )
                            if basename in basenames:
                                raise ValueError(f"duplicate payload basename in {spec.slug}: {basename}")
                            basenames.add(basename)

                            source_file = source_tar.extractfile(source_member)
                            if source_file is None:
                                raise ValueError(f"cannot read archive member: {source_member.name}")
                            hashing_reader = HashingReader(source_file)
                            member_path = f"{spec.slug}/raw/{basename}"
                            output_tar.addfile(
                                normalized_tar_info(member_path, source_member.size),
                                hashing_reader,
                            )
                            if hashing_reader.bytes_read != source_member.size:
                                raise IOError(f"short read for {source_member.name}")
                            members.append(
                                {
                                    "path": member_path,
                                    "bytes": source_member.size,
                                    "sha256": hashing_reader.hexdigest(),
                                }
                            )
                            raw_bytes += source_member.size

                    if not members:
                        raise ValueError(f"source archive has no regular payload members: {spec.source_path}")
                    metadata = _embedded_metadata(spec, source_sha256, members)
                    encoded = (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
                    output_tar.addfile(
                        normalized_tar_info(f"{spec.slug}/metadata.json", len(encoded)),
                        io.BytesIO(encoded),
                    )
        partial.replace(output_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    return {
        **_embedded_metadata(spec, source_sha256, members),
        "archive_sha256": file_sha256(output_path),
        "archive_bytes": output_path.stat().st_size,
        "raw_bytes": raw_bytes,
        "file_count": len(members),
        "status": "complete",
    }


def default_specs(hpc_root: Path, text_root: Path) -> list[DatasetSpec]:
    return [
        DatasetSpec(
            "cesm_atm",
            "CESM-ATM Dataset1",
            "scientific_numeric",
            "climate",
            hpc_root / "cesm_atm_dataset1/source/SDRBENCH-CESM-ATM-1800x3600.tar.gz",
            "https://sdrbench.github.io/",
            "float32",
            (1800, 3600),
            "little",
        ),
        DatasetSpec(
            "hurricane_isabel",
            "Hurricane Isabel",
            "scientific_numeric",
            "weather",
            hpc_root / "hurricane_isabel/source/SDRBENCH-Hurricane-ISABEL-100x500x500.tar.gz",
            "https://sdrbench.github.io/",
            "float32",
            (100, 500, 500),
            "little",
        ),
        DatasetSpec(
            "miranda",
            "Miranda",
            "scientific_numeric",
            "fluid_dynamics",
            hpc_root / "miranda/source/SDRBENCH-Miranda-256x384x384.tar.gz",
            "https://sdrbench.github.io/",
            "float64",
            (256, 384, 384),
            "little",
        ),
        DatasetSpec(
            "nyx",
            "NYX",
            "scientific_numeric",
            "cosmology_grid",
            hpc_root / "nyx/source/SDRBENCH-EXASKY-NYX-512x512x512.tar.gz",
            "https://sdrbench.github.io/",
            "float32",
            (512, 512, 512),
            "little",
        ),
        DatasetSpec(
            "hacc",
            "HACC medium",
            "scientific_numeric",
            "cosmology_particles",
            hpc_root / "hacc_medium/source/EXASKY-HACC-data-medium-size.tar.gz",
            "https://sdrbench.github.io/",
            "float32",
            (280_953_867,),
            "little",
        ),
        DatasetSpec(
            "scale_letkf",
            "SCALE-LETKF",
            "scientific_numeric",
            "weather_ensemble",
            hpc_root / "scale_letkf/source/SDRBENCH-SCALE-98x1200x1200.tar.gz",
            "https://sdrbench.github.io/",
            "float32",
            (98, 1200, 1200),
            "little",
        ),
        DatasetSpec(
            "exafel",
            "EXAFEL",
            "scientific_numeric",
            "xray_detector",
            hpc_root / "exafel/source/SDRBENCH-EXAFEL-10x32x185x388.tar.gz",
            "https://sdrbench.github.io/",
            "uint16",
            (10, 32, 185, 388),
            "little",
        ),
        DatasetSpec(
            "rcsb_mmcif",
            "RCSB mmCIF",
            "byte_stream",
            "biomolecular_structure",
            text_root / "AI/ai_rcsb_mmcif_prepared_raw_sample.tar.zst",
            "oss://science-compressor-datasets/AI Dataset/ai_rcsb_mmcif_prepared_raw_sample.tar.zst",
        ),
        DatasetSpec(
            "ncbi_grch38_fasta",
            "NCBI Human GRCh38 FASTA",
            "byte_stream",
            "genomics",
            text_root / "HPC/hpc_ncbi_human_grch38_fasta_prepared_raw_sample.tar.zst",
            "oss://science-compressor-datasets/HPC Dataset/hpc_ncbi_human_grch38_fasta_prepared_raw_sample.tar.zst",
        ),
        DatasetSpec(
            "redpajama_jsonl",
            "RedPajama JSONL",
            "byte_stream",
            "natural_language",
            text_root / "AI/ai_redpajama_jsonl_text_prepared_raw_sample.tar.zst",
            "oss://science-compressor-datasets/AI Dataset/ai_redpajama_jsonl_text_prepared_raw_sample.tar.zst",
        ),
        DatasetSpec(
            "the_stack_jsonl",
            "The Stack code JSONL",
            "byte_stream",
            "source_code",
            text_root / "AI/ai_the_stack_code_jsonl_prepared_raw_sample.tar.zst",
            "oss://science-compressor-datasets/AI Dataset/ai_the_stack_code_jsonl_prepared_raw_sample.tar.zst",
        ),
        DatasetSpec(
            "noaa_gsod_csv",
            "NOAA GSOD CSV",
            "byte_stream",
            "weather_observations",
            text_root / "HPC/hpc_noaa_gsod_2024_text_prepared_raw_sample.tar.zst",
            "oss://science-compressor-datasets/HPC Dataset/hpc_noaa_gsod_2024_text_prepared_raw_sample.tar.zst",
        ),
    ]


def write_bundle_metadata(output_root: Path, records: list[dict[str, object]]) -> None:
    metadata_dir = output_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "bundle": "dcc-full-v1",
        "oss_prefix": "oss://science-compressor-datasets/DCC/full-v1/",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_count": len(records),
        "datasets": records,
    }
    (metadata_dir / "datasets.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    columns = [
        "slug",
        "display_name",
        "domain",
        "category",
        "dtype",
        "shape",
        "endian",
        "archive",
        "archive_bytes",
        "raw_bytes",
        "file_count",
        "archive_sha256",
        "source_archive",
        "source_archive_sha256",
        "source_url",
    ]
    with (metadata_dir / "datasets.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["shape"] = ",".join(str(item) for item in record.get("shape") or [])
            writer.writerow(row)

    with (metadata_dir / "SHA256SUMS").open("w", encoding="ascii", newline="\n") as handle:
        for record in records:
            handle.write(f"{record['archive_sha256']}  {record['archive']}\n")


def write_bundle_support_files(output_root: Path) -> None:
    script_dir = Path(__file__).resolve().parent
    tools_dir = output_root / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(script_dir / "prepare_dcc_datasets.py", tools_dir / "prepare_datasets.py")
    (output_root / "requirements.txt").write_text("zstandard>=0.22,<1\n", encoding="ascii")
    (output_root / "README.md").write_text(
        """# DCC full-v1 datasets

List available datasets:

```bash
python tools/prepare_datasets.py --list
```

Verify and extract selected datasets:

```bash
python tools/prepare_datasets.py --output ./datasets --select cesm_atm hurricane_isabel rcsb_mmcif
```

Extract all datasets by omitting `--select`. Compression experiments must read files below
`datasets/<slug>/raw/`, never the `.tar.zst` transport archives. Typed codec experiments must
use the dtype, shape, and endian values from `metadata/datasets.json`.
""",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the standardized DCC full-v1 dataset bundle.")
    parser.add_argument("--output", type=Path, default=Path(r"E:\Dataset\DCC_upload\full-v1"))
    parser.add_argument("--hpc-root", type=Path, default=Path(r"E:\Dataset\HPC_Dataset"))
    parser.add_argument("--text-root", type=Path, default=Path(r"E:\Dataset\实验数据集"))
    parser.add_argument("--only", action="append", help="Build only this slug; can be repeated.")
    parser.add_argument("--compression-level", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output.resolve()
    archives_dir = output_root / "archives"
    records_dir = output_root / "metadata" / "records"
    archives_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)
    selected = set(args.only or [])
    specs = default_specs(args.hpc_root.resolve(), args.text_root.resolve())
    if selected:
        unknown = selected - {spec.slug for spec in specs}
        if unknown:
            raise SystemExit(f"unknown dataset slug(s): {', '.join(sorted(unknown))}")
        specs = [spec for spec in specs if spec.slug in selected]

    records: list[dict[str, object]] = []
    for index, spec in enumerate(specs, 1):
        output_archive = archives_dir / f"{spec.slug}.tar.zst"
        record_path = records_dir / f"{spec.slug}.json"
        record: dict[str, object] | None = None
        if output_archive.exists() and record_path.exists() and not args.force:
            candidate = json.loads(record_path.read_text(encoding="utf-8"))
            if (
                candidate.get("archive_bytes") == output_archive.stat().st_size
                and candidate.get("archive_sha256") == file_sha256(output_archive)
            ):
                record = candidate
                print(f"[{index}/{len(specs)}] reuse {spec.slug}", flush=True)
        if record is None:
            print(f"[{index}/{len(specs)}] build {spec.slug} from {spec.source_path}", flush=True)
            record = build_dataset_archive(spec, output_archive, compression_level=args.compression_level)
            record["archive"] = f"archives/{output_archive.name}"
            record_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        records.append(record)
        write_bundle_metadata(output_root, records)

    write_bundle_metadata(output_root, records)
    write_bundle_support_files(output_root)
    print(f"bundle complete: {output_root} datasets={len(records)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import io
import json
import struct
import tarfile
from pathlib import Path

import pytest
import zstandard as zstd

from Script.build_dcc_dataset_bundle import (
    DatasetSpec,
    build_dataset_archive,
    expected_field_bytes,
    safe_member_basename,
)
from Script.prepare_dcc_datasets import extract_dataset_archive


def _write_source_tar_gz(path: Path, member_name: str, payload: bytes) -> None:
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))


def _write_source_tar_gz_members(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member_name, payload in members.items():
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _zstd_tar_members(path: Path) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with path.open("rb") as source:
        with zstd.ZstdDecompressor().stream_reader(source) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                for item in archive:
                    if item.isfile():
                        extracted = archive.extractfile(item)
                        assert extracted is not None
                        members[item.name] = extracted.read()
    return members


@pytest.mark.parametrize("name", ["../field.f32", "/field.f32", "C:/field.f32", "folder/../field.f32"])
def test_safe_member_basename_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(ValueError, match="unsafe archive member"):
        safe_member_basename(name)


def test_expected_field_bytes_uses_dtype_and_shape() -> None:
    assert expected_field_bytes("float32", (2, 3, 4)) == 96
    assert expected_field_bytes("float64", (2, 3, 4)) == 192
    assert expected_field_bytes("uint16", (2, 3, 4)) == 48


def test_build_and_extract_standardized_numeric_archive(tmp_path: Path) -> None:
    payload = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
    source = tmp_path / "source.tar.gz"
    output = tmp_path / "tiny_numeric.tar.zst"
    _write_source_tar_gz(source, "upstream/field.f32", payload)
    spec = DatasetSpec(
        slug="tiny_numeric",
        display_name="Tiny Numeric",
        domain="scientific_numeric",
        category="test",
        source_path=source,
        source_url="https://example.invalid/source.tar.gz",
        dtype="float32",
        shape=(2, 2),
        endian="little",
    )

    record = build_dataset_archive(spec, output)

    assert record["raw_bytes"] == len(payload)
    assert record["file_count"] == 1
    members = _zstd_tar_members(output)
    assert members["tiny_numeric/raw/field.f32"] == payload
    embedded = json.loads(members["tiny_numeric/metadata.json"])
    assert embedded["dtype"] == "float32"
    assert embedded["shape"] == [2, 2]
    assert embedded["members"][0]["path"] == "tiny_numeric/raw/field.f32"

    manifest_record = {**record, "archive": "archives/tiny_numeric.tar.zst"}
    extracted_root = tmp_path / "datasets"
    extract_dataset_archive(output, extracted_root, manifest_record)
    assert (extracted_root / "tiny_numeric" / "raw" / "field.f32").read_bytes() == payload


def test_build_rejects_numeric_member_with_wrong_size(tmp_path: Path) -> None:
    source = tmp_path / "source.tar.gz"
    _write_source_tar_gz(source, "upstream/field.f32", b"too short")
    spec = DatasetSpec(
        slug="bad_numeric",
        display_name="Bad Numeric",
        domain="scientific_numeric",
        category="test",
        source_path=source,
        source_url="https://example.invalid/source.tar.gz",
        dtype="float32",
        shape=(2, 2),
        endian="little",
    )

    with pytest.raises(ValueError, match="expected 16 bytes"):
        build_dataset_archive(spec, tmp_path / "bad.tar.zst")


def test_build_numeric_archive_skips_non_payload_members(tmp_path: Path) -> None:
    payload = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
    source = tmp_path / "mixed.tar.gz"
    _write_source_tar_gz_members(
        source,
        {
            "upstream/template_data.txt": b"upstream metadata",
            "upstream/field.f32": payload,
        },
    )
    spec = DatasetSpec(
        slug="mixed_numeric",
        display_name="Mixed Numeric",
        domain="scientific_numeric",
        category="test",
        source_path=source,
        source_url="https://example.invalid/mixed.tar.gz",
        dtype="float32",
        shape=(2, 2),
        endian="little",
    )

    record = build_dataset_archive(spec, tmp_path / "mixed.tar.zst")

    assert record["file_count"] == 1
    assert [member["path"] for member in record["members"]] == ["mixed_numeric/raw/field.f32"]

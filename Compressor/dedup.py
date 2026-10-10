from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator


@dataclass
class BlockRef:
    block_index: int
    offset: int
    size_bytes: int
    block_hash: str
    is_duplicate_block: bool
    unique_block_id: str


@dataclass
class FileRecord:
    file_id: str
    rel_path: str
    size_bytes: int
    sha256: str
    is_duplicate_file: bool
    duplicate_of_file_id: str | None
    blocks: list[BlockRef] = field(default_factory=list)
    mode: int | None = None
    mtime_ns: int | None = None


@dataclass
class UniqueBlockRecord:
    unique_block_id: str
    block_hash: str
    raw_size: int
    source_file_id: str
    source_block_index: int
    raw_path: str | None = None
    cpss_path: str | None = None
    compressed_size: int | None = None
    pipeline_name: str | None = None
    pipeline_id: int | None = None
    compress_time_s: float | None = None
    cpss_sha256: str | None = None


@dataclass
class DedupStats:
    raw_bytes: int = 0
    file_count: int = 0
    unique_file_count: int = 0
    duplicate_file_count: int = 0
    file_dedup_saved_bytes: int = 0
    block_total_count: int = 0
    block_unique_count: int = 0
    block_duplicate_count: int = 0
    block_dedup_saved_bytes: int = 0
    unique_raw_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "raw_bytes": self.raw_bytes,
            "file_count": self.file_count,
            "unique_file_count": self.unique_file_count,
            "duplicate_file_count": self.duplicate_file_count,
            "file_dedup_saved_bytes": self.file_dedup_saved_bytes,
            "block_total_count": self.block_total_count,
            "block_unique_count": self.block_unique_count,
            "block_duplicate_count": self.block_duplicate_count,
            "block_dedup_saved_bytes": self.block_dedup_saved_bytes,
            "unique_raw_bytes": self.unique_raw_bytes,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def iter_file_blocks(handle: BinaryIO, block_size: int) -> Iterator[tuple[int, bytes]]:
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    offset = 0
    while True:
        payload = handle.read(block_size)
        if not payload:
            return
        yield offset, payload
        offset += len(payload)


def iter_blocks(path: Path, block_size: int) -> Iterator[tuple[int, bytes]]:
    with path.open("rb") as handle:
        yield from iter_file_blocks(handle, block_size)


def _relative_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise NotADirectoryError(root)
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def scan_directory(root: Path, block_size: int) -> tuple[list[FileRecord], list[UniqueBlockRecord], DedupStats]:
    root = root.resolve()
    if block_size <= 0:
        raise ValueError("block_size must be positive")

    files: list[FileRecord] = []
    unique_blocks: list[UniqueBlockRecord] = []
    stats = DedupStats()
    file_index: dict[tuple[int, str], FileRecord] = {}
    block_index: dict[tuple[int, str], UniqueBlockRecord] = {}

    for file_number, path in enumerate(_relative_files(root), start=1):
        stat = path.stat()
        file_sha = sha256_file(path)
        rel_path = path.relative_to(root).as_posix()
        record = FileRecord(
            file_id=f"file_{file_number:06d}",
            rel_path=rel_path,
            size_bytes=stat.st_size,
            sha256=file_sha,
            is_duplicate_file=False,
            duplicate_of_file_id=None,
            mode=stat.st_mode & 0o7777,
            mtime_ns=stat.st_mtime_ns,
        )
        stats.file_count += 1
        stats.raw_bytes += stat.st_size
        prior_file = file_index.get((stat.st_size, file_sha))
        if prior_file is not None:
            record.is_duplicate_file = True
            record.duplicate_of_file_id = prior_file.file_id
            record.blocks = [
                BlockRef(
                    block_index=block.block_index,
                    offset=block.offset,
                    size_bytes=block.size_bytes,
                    block_hash=block.block_hash,
                    is_duplicate_block=True,
                    unique_block_id=block.unique_block_id,
                )
                for block in prior_file.blocks
            ]
            stats.duplicate_file_count += 1
            stats.file_dedup_saved_bytes += stat.st_size
            files.append(record)
            continue

        file_index[(stat.st_size, file_sha)] = record
        stats.unique_file_count += 1
        for block_index_number, (offset, payload) in enumerate(iter_blocks(path, block_size)):
            block_sha = sha256_bytes(payload)
            key = (len(payload), block_sha)
            prior_block = block_index.get(key)
            if prior_block is None:
                unique_id = f"block_{len(unique_blocks) + 1:06d}"
                prior_block = UniqueBlockRecord(
                    unique_block_id=unique_id,
                    block_hash=block_sha,
                    raw_size=len(payload),
                    source_file_id=record.file_id,
                    source_block_index=block_index_number,
                )
                block_index[key] = prior_block
                unique_blocks.append(prior_block)
                duplicate = False
                stats.block_unique_count += 1
                stats.unique_raw_bytes += len(payload)
            else:
                duplicate = True
                stats.block_duplicate_count += 1
                stats.block_dedup_saved_bytes += len(payload)
            record.blocks.append(
                BlockRef(
                    block_index=block_index_number,
                    offset=offset,
                    size_bytes=len(payload),
                    block_hash=block_sha,
                    is_duplicate_block=duplicate,
                    unique_block_id=prior_block.unique_block_id,
                )
            )
            stats.block_total_count += 1
        files.append(record)

    return files, unique_blocks, stats


def read_block(path: Path, ref: BlockRef) -> bytes:
    with path.open("rb") as handle:
        handle.seek(ref.offset)
        payload = handle.read(ref.size_bytes)
    if len(payload) != ref.size_bytes:
        raise ValueError(f"short block read for {path}: expected {ref.size_bytes}, got {len(payload)}")
    if sha256_bytes(payload) != ref.block_hash:
        raise ValueError(f"block hash mismatch for {path} at offset {ref.offset}")
    return payload


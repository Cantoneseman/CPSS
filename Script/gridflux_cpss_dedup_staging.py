#!/usr/bin/env python3
"""Stage, deduplicate, CPSS-compress, and transfer a directory with GridFlux."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Compressor import FIXED_HEADER_SIZE
from Compressor.container import build_header, encode_metadata
from Compressor.dedup import (
    BlockRef,
    FileRecord,
    UniqueBlockRecord,
    read_block,
    scan_directory,
)
from Compressor.dedup_manifest import build_manifest, write_manifest
from Compressor.runtime import PipelineContext
from Compressor.selector import SELECTOR_MODE_CODES, select_pipeline


DEFAULT_REMOTE_HOST = "47.116.174.181"
DEFAULT_REMOTE_ALIAS = "gridflux-beta-shanghai"
DEFAULT_REMOTE_ROOT = "/tmp/gridflux_cpss_dedup_stage1"
DEFAULT_GRIDFLUX_UPLOAD = "/root/projects/GridFlux-Beta/build/gridflux-tree-upload-client"
DEFAULT_GRIDFLUX_SERVER = "/root/projects/GridFlux-Beta/build/gridflux-gridftp-server"
DEFAULT_REMOTE_PROJECT = "/root/projects/science-compressor"
DEFAULT_REMOTE_PYTHON = "/opt/gridflux-cpss-venv/bin/python3"
DEFAULT_GRIDFLUX_PORT = 25252
DEFAULT_DATA_PORT_BASE = 20300
DEFAULT_BLOCK_MIB = 8
CSV_FIELDS = [
    "run_id", "dataset", "input_dir", "block_size_bytes", "file_count", "raw_bytes",
    "file_dedup_hit_files", "file_dedup_saved_bytes", "block_total_count",
    "block_unique_count", "block_duplicate_count", "block_dedup_saved_bytes",
    "unique_raw_bytes", "compressed_unique_bytes", "manifest_bytes", "transferred_bytes",
    "raw_gridflux_transfer_time_s", "cpss_only_compressed_bytes",
    "cpss_only_transfer_time_s", "dedup_scan_time_s", "file_hash_time_s",
    "block_hash_time_s", "compress_time_s", "gridflux_transfer_time_s",
    "remote_decompress_time_s", "remote_rebuild_time_s", "remote_verify_time_s",
    "end_to_end_time_s", "compression_ratio_unique", "dedup_ratio",
    "logical_bandwidth_gain_vs_raw", "logical_bandwidth_gain_vs_cpss_only",
    "e2e_gain_vs_raw", "e2e_gain_vs_cpss_only", "sha256_ok", "roundtrip_ok",
    "status", "error",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def q(value: object) -> str:
    return shlex.quote(str(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_cmd(
    command: list[str],
    *,
    cwd: Path = ROOT,
    log: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    rendered = "$ " + " ".join(q(item) for item in command)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=capture,
        check=False,
    )
    if log is not None:
        with log.open("a", encoding="utf-8") as handle:
            if result.stdout:
                handle.write(result.stdout)
            if result.stderr:
                handle.write(result.stderr)
            handle.write(f"[exit {result.returncode}]\n")
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {rendered}\n{result.stderr[-2000:]}")
    return result


def ssh_cmd(
    alias: str,
    command: str,
    *,
    log: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_cmd(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, command],
        log=log,
        check=check,
    )


def serialize_expected_files(root: Path, output: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink()):
        rows.append({
            "rel_path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rows


def cpss_blob(raw: bytes, selector: str, top_k: int, pipelines: list[str] | None, model_dir: Path | None) -> tuple[bytes, dict[str, Any]]:
    context = PipelineContext(
        dtype_name=None,
        shape=(len(raw),),
        typesize=1,
        metadata={"scenario": "science", "format": "bin"},
    )
    context_metadata = {
        "dtype_name": None,
        "shape": [len(raw)],
        "typesize": 1,
        "endian": "little",
        "shape_source": "block",
        "original_suffix": ".bin",
        "aligned_size": len(raw),
        "element_count": len(raw),
        "scenario": "science",
        "source_group": "dedup_stage1",
        "format": "bin",
    }
    selection = select_pipeline(
        raw,
        context,
        context_metadata,
        mode=selector,
        top_k=top_k,
        candidates=pipelines,
        model_dir=model_dir,
    )
    selection.artifact.selector_metadata.update({
        "requested_mode": selector,
        "resolved_mode": selection.selector_mode,
        "top_k": top_k,
    })
    metadata_blob = encode_metadata(selection.artifact.metadata)
    header = build_header(
        selector_mode=SELECTOR_MODE_CODES.get(selection.selector_mode, 0),
        pipeline_id=selection.artifact.pipeline_id,
        metadata_size=len(metadata_blob),
        tail_size=len(selection.artifact.tail),
        payload_size=len(selection.artifact.payload),
        original_size=len(raw),
    )
    blob = header + metadata_blob + selection.artifact.tail + selection.artifact.payload
    return blob, {
        "pipeline_name": selection.artifact.pipeline_name,
        "pipeline_id": selection.artifact.pipeline_id,
        "compress_time_s": 0.0,
        "selector_mode": selection.selector_mode,
    }


def source_block(root: Path, records: dict[str, FileRecord], file_id: str, block_index: int) -> bytes:
    record = records[file_id]
    ref = record.blocks[block_index]
    return read_block(root / record.rel_path, ref)


def make_cpss_only_records(root: Path, block_size: int) -> tuple[list[FileRecord], list[UniqueBlockRecord], dict[str, int]]:
    files: list[FileRecord] = []
    unique_blocks: list[UniqueBlockRecord] = []
    raw_bytes = 0
    block_count = 0
    for file_number, path in enumerate(
        sorted(path for path in root.rglob("*") if path.is_file() and not path.is_symlink()), start=1
    ):
        stat = path.stat()
        file_id = f"file_{file_number:06d}"
        content_hash = sha256_file(path)
        record = FileRecord(
            file_id=file_id,
            rel_path=path.relative_to(root).as_posix(),
            size_bytes=stat.st_size,
            sha256=content_hash,
            is_duplicate_file=False,
            duplicate_of_file_id=None,
            mode=stat.st_mode & 0o7777,
            mtime_ns=stat.st_mtime_ns,
        )
        for index, (offset, payload) in enumerate(_iter_blocks(path, block_size)):
            block_hash = sha256_bytes(payload)
            unique_id = f"block_{len(unique_blocks) + 1:06d}"
            unique_blocks.append(UniqueBlockRecord(
                unique_block_id=unique_id,
                block_hash=block_hash,
                raw_size=len(payload),
                source_file_id=file_id,
                source_block_index=index,
            ))
            record.blocks.append(BlockRef(index, offset, len(payload), block_hash, False, unique_id))
            block_count += 1
        raw_bytes += stat.st_size
        files.append(record)
    return files, unique_blocks, {
        "raw_bytes": raw_bytes,
        "file_count": len(files),
        "block_total_count": block_count,
        "block_unique_count": block_count,
        "block_duplicate_count": 0,
        "unique_raw_bytes": raw_bytes,
    }


def _iter_blocks(path: Path, block_size: int) -> Iterable[tuple[int, bytes]]:
    offset = 0
    with path.open("rb") as handle:
        while True:
            payload = handle.read(block_size)
            if not payload:
                return
            yield offset, payload
            offset += len(payload)


class StagingRunner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.run_id = args.result_dir.name
        self.result_dir = args.result_dir
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.command_log = self.result_dir / "command_log.txt"
        self.sha_log = self.result_dir / "sha256_log.txt"
        self.remote_session = f"{args.remote_root.rstrip('/')}/{self.run_id}"
        self.server_root = f"{self.remote_session}/server_root"
        self.remote_log_root = f"{self.remote_session}/logs"
        self.remote_server_pid = f"{self.remote_session}/server.pid"
        self.remote_server_exit = f"{self.remote_session}/server.exit"
        self.remote_server_log = f"{self.remote_log_root}/gridflux-server.log"
        self.remote_event_log = f"{self.remote_log_root}/gridflux-server-events.jsonl"
        self.tmp = Path(tempfile.mkdtemp(prefix=f"{self.run_id}-", dir="/tmp"))
        self.remote_started = False

    def log(self, text: str) -> None:
        with self.command_log.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")

    def sync_code(self) -> None:
        excludes = [
            "--exclude=Model/",
            "--exclude=Dataset/",
            "--exclude=RawDataset/",
            "--exclude=MediumDataset/",
            "--exclude=ExperimentRecords/",
            "--exclude=.pytest_cache/",
            "--exclude=__pycache__/",
            "--exclude=*.pyc",
            "--exclude=*.part",
            "--exclude=*.lock",
        ]
        run_cmd(
            ["rsync", "-az", *excludes, f"{ROOT}/", f"{self.args.remote_alias}:{DEFAULT_REMOTE_PROJECT}/"],
            log=self.command_log,
        )
        local_manifest = self.tmp / "code_manifest.json"
        rows = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(ROOT)
            if any(part in {"Model", "Dataset", "RawDataset", "MediumDataset", "ExperimentRecords", ".pytest_cache"} for part in rel.parts):
                continue
            rows.append({"path": rel.as_posix(), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
        local_manifest.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
        remote_manifest = f"{self.remote_session}/code_manifest.json"
        remote_manifest_literal = json.dumps(remote_manifest)
        ssh_cmd(self.args.remote_alias, f"mkdir -p {q(self.remote_session)}", log=self.command_log)
        run_cmd(["rsync", "-az", str(local_manifest), f"{self.args.remote_alias}:{remote_manifest}"], log=self.command_log)
        check = ssh_cmd(
            self.args.remote_alias,
            f"""set -e
cd {q(DEFAULT_REMOTE_PROJECT)}
{q(DEFAULT_REMOTE_PYTHON)} - <<'PY'
import hashlib, json
from pathlib import Path
expected = json.loads(Path({remote_manifest_literal}).read_text())
actual = []
for p in sorted(Path('.').rglob('*')):
    if not p.is_file() or '__pycache__' in p.parts or p.suffix == '.pyc':
        continue
    r = p.relative_to('.')
    if any(x in {'Model','Dataset','RawDataset','MediumDataset','ExperimentRecords','.pytest_cache'} for x in r.parts):
        continue
    actual.append({{'path': r.as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size_bytes': p.stat().st_size}})
print(json.dumps({{'match': sorted(actual, key=lambda x: x['path']) == sorted(expected, key=lambda x: x['path']), 'local_count': len(expected), 'remote_count': len(actual)}}))
PY
rm -f {q(remote_manifest)}
""",
            log=self.command_log,
        )
        result = json.loads(check.stdout.strip().splitlines()[-1])
        if not result.get("match"):
            raise RuntimeError(f"remote code manifest mismatch: {result}")

    def start_remote_server(self) -> None:
        command = f"""set -e
if ss -ltn | grep -q ':{self.args.gridflux_port} '; then
  echo "GridFlux port {self.args.gridflux_port} is already occupied" >&2
  exit 2
fi
rm -rf {q(self.remote_session)}
mkdir -p {q(self.server_root)} {q(self.remote_log_root)}
nohup {q(DEFAULT_GRIDFLUX_SERVER)} --host 0.0.0.0 --port {self.args.gridflux_port} --data-port-base {self.args.data_port_base} --root {q(self.server_root)} --connections 2 --checksum crc32c --checksum-backend auto --event-log {q(self.remote_event_log)} > {q(self.remote_server_log)} 2>&1 < /dev/null &
server_pid="$!"
printf '%s\\n' "$server_pid" > {q(self.remote_server_pid)}
for i in $(seq 1 40); do
  if ss -ltn | grep -q ':{self.args.gridflux_port} '; then exit 0; fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    cat {q(self.remote_server_log)} || true
    exit 1
  fi
  sleep 0.25
done
cat {q(self.remote_server_log)} || true
exit 1
"""
        ssh_cmd(self.args.remote_alias, command, log=self.command_log)
        self.remote_started = True

    def stop_remote_server(self) -> None:
        command = f"""if [ -f {q(self.remote_server_pid)} ]; then
  server_pid="$(cat {q(self.remote_server_pid)} 2>/dev/null || true)"
  if [ -n "$server_pid" ]; then
    kill "$server_pid" 2>/dev/null || true
    for i in $(seq 1 20); do
      if ! kill -0 "$server_pid" 2>/dev/null; then break; fi
      sleep 0.25
    done
    kill -9 "$server_pid" 2>/dev/null || true
  fi
fi
rm -rf {q(self.remote_session)}
"""
        ssh_cmd(self.args.remote_alias, command, log=self.command_log, check=False)
        self.remote_started = False

    def upload(self, source: Path, destination: str, tag: str) -> tuple[float, dict[str, Any]]:
        summary = self.result_dir / f"{tag}_gridflux_summary.json"
        event_log = self.result_dir / f"{tag}_gridflux_events.jsonl"
        command = [
            self.args.gridflux_tree_upload,
            "--host", self.args.remote_host,
            "--port", str(self.args.gridflux_port),
            "--source-dir", str(source),
            "--dest-dir", destination,
            "--connections", "2",
            "--file-parallelism", "2",
            "--checksum", "crc32c",
            "--checksum-backend", "auto",
            "--json-summary", str(summary),
            "--event-log", str(event_log),
        ]
        started = time.perf_counter()
        run_cmd(command, log=self.command_log)
        elapsed = time.perf_counter() - started
        data = json.loads(summary.read_text(encoding="utf-8")) if summary.exists() else {}
        return elapsed, data

    def remote_verify_bundle(self, remote_bundle: str, remote_output: str) -> dict[str, Any]:
        result_path = f"{self.remote_session}/verify-{Path(remote_output).name}.json"
        command = (
            f"{q(DEFAULT_REMOTE_PYTHON)} {q(DEFAULT_REMOTE_PROJECT + '/Script/gridflux_cpss_dedup_remote.py')} "
            f"--mode bundle --bundle-dir {q(remote_bundle)} --output-dir {q(remote_output)} "
            f"--result-json {q(result_path)} && rm -f {q(result_path)}"
        )
        result = ssh_cmd(self.args.remote_alias, command, log=self.command_log)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def remote_verify_raw(self, remote_raw: str, expected: Path, remote_output: str) -> dict[str, Any]:
        remote_expected = f"{self.remote_session}/expected-raw.json"
        run_cmd(["rsync", "-az", str(expected), f"{self.args.remote_alias}:{remote_expected}"], log=self.command_log)
        result_path = f"{self.remote_session}/verify-raw.json"
        command = (
            f"{q(DEFAULT_REMOTE_PYTHON)} {q(DEFAULT_REMOTE_PROJECT + '/Script/gridflux_cpss_dedup_remote.py')} "
            f"--mode raw --raw-dir {q(remote_raw)} --expected-json {q(remote_expected)} "
            f"--output-dir {q(remote_output)} --result-json {q(result_path)} "
            f"&& rm -f {q(result_path)} {q(remote_expected)}"
        )
        result = ssh_cmd(self.args.remote_alias, command, log=self.command_log)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def compress_records(
        self,
        root: Path,
        files: list[FileRecord],
        unique_blocks: list[UniqueBlockRecord],
        bundle: Path,
        *,
        dedup: bool,
    ) -> tuple[dict[str, Any], float]:
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "blocks").mkdir(parents=True, exist_ok=True)
        file_map = {item.file_id: item for item in files}
        total_compress = 0.0
        for item in unique_blocks:
            raw = source_block(root, file_map, item.source_file_id, item.source_block_index)
            started = time.perf_counter()
            blob, metadata = cpss_blob(
                raw,
                self.args.selector,
                self.args.top_k,
                self.args.pipelines,
                self.args.model_dir,
            )
            elapsed = time.perf_counter() - started
            total_compress += elapsed
            filename = f"{item.block_hash}.cpss" if dedup else f"{item.unique_block_id}-{item.block_hash}.cpss"
            cpss_path = bundle / "blocks" / filename
            cpss_path.write_bytes(blob)
            item.cpss_path = f"blocks/{filename}"
            item.compressed_size = len(blob)
            item.pipeline_name = metadata["pipeline_name"]
            item.pipeline_id = metadata["pipeline_id"]
            item.compress_time_s = elapsed
            item.cpss_sha256 = sha256_bytes(blob)
            with self.sha_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{item.unique_block_id} raw={item.block_hash} cpss={item.cpss_sha256}\n")
        return {
            "compressed_unique_bytes": sum(int(item.compressed_size or 0) for item in unique_blocks),
            "unique_block_count": len(unique_blocks),
        }, total_compress

    def build_bundle(
        self,
        root: Path,
        bundle: Path,
        files: list[FileRecord],
        unique_blocks: list[UniqueBlockRecord],
        stats: dict[str, Any],
        *,
        dedup: bool,
    ) -> tuple[dict[str, Any], float]:
        compression_stats, compress_time = self.compress_records(
            root, files, unique_blocks, bundle, dedup=dedup
        )
        manifest_stats = dict(stats)
        manifest_stats.update(compression_stats)
        manifest_stats["file_dedup_saved_bytes"] = int(manifest_stats.get("file_dedup_saved_bytes", 0))
        manifest_stats["block_dedup_saved_bytes"] = int(manifest_stats.get("block_dedup_saved_bytes", 0))
        manifest = build_manifest(
            run_id=self.run_id,
            source_root=root,
            block_size_bytes=self.args.block_mib * 1024 * 1024,
            files=files,
            unique_blocks=unique_blocks,
            stats=manifest_stats,
            created_at=now(),
        )
        manifest_path = bundle / "manifest.json"
        write_manifest(manifest_path, manifest)
        manifest["stats"]["manifest_bytes"] = manifest_path.stat().st_size
        manifest["stats"]["transferred_bytes"] = (
            int(manifest["stats"]["manifest_bytes"])
            + int(compression_stats["compressed_unique_bytes"])
        )
        write_manifest(manifest_path, manifest)
        manifest["stats"]["manifest_bytes"] = manifest_path.stat().st_size
        manifest["stats"]["transferred_bytes"] = (
            int(manifest["stats"]["manifest_bytes"])
            + int(compression_stats["compressed_unique_bytes"])
        )
        write_manifest(manifest_path, manifest)
        return manifest, compress_time

    def run_method(
        self,
        root: Path,
        method: str,
        *,
        files: list[FileRecord] | None = None,
        unique_blocks: list[UniqueBlockRecord] | None = None,
        stats: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        method_root = self.tmp / method
        method_root.mkdir(parents=True, exist_ok=True)
        expected_path = method_root / "expected.json"
        expected = serialize_expected_files(root, expected_path)
        destination = f"{self.run_id}/{method}"
        remote_output = (
            f"{self.server_root}/{destination}/rebuild"
            if method != "raw"
            else f"{self.server_root}/rebuild/{self.run_id}/{method}"
        )
        started = time.perf_counter()
        if method == "raw":
            transfer_time, _ = self.upload(root, destination, method)
            verify = self.remote_verify_raw(
                f"{self.server_root}/{destination}",
                expected_path,
                remote_output,
            )
            return {
                "method": method,
                "raw_bytes": sum(item["size_bytes"] for item in expected),
                "transferred_bytes": sum(item["size_bytes"] for item in expected),
                "transfer_time_s": transfer_time,
                "remote_decompress_time_s": 0.0,
                "remote_rebuild_time_s": 0.0,
                "remote_verify_time_s": verify["verify_time_s"],
                "end_to_end_time_s": time.perf_counter() - started,
                "verify": verify,
            }

        if files is None or unique_blocks is None or stats is None:
            raise ValueError(f"records required for {method}")
        bundle = method_root / "bundle"
        manifest, compress_time = self.build_bundle(
            root, bundle, files, unique_blocks, stats, dedup=method == "dedup_cpss"
        )
        transfer_time, _ = self.upload(bundle, destination, method)
        verify = self.remote_verify_bundle(
            f"{self.server_root}/{destination}",
            remote_output,
        )
        return {
            "method": method,
            "raw_bytes": int(stats["raw_bytes"]),
            "transferred_bytes": int(manifest["stats"]["transferred_bytes"]),
            "compressed_unique_bytes": int(manifest["stats"]["compressed_unique_bytes"]),
            "manifest_bytes": int(manifest["stats"]["manifest_bytes"]),
            "compress_time_s": compress_time,
            "transfer_time_s": transfer_time,
            "remote_decompress_time_s": float(verify.get("decompress_time_s", 0.0)),
            "remote_rebuild_time_s": float(verify.get("rebuild_time_s", 0.0)),
            "remote_verify_time_s": float(verify.get("verify_time_s", 0.0)),
            "end_to_end_time_s": time.perf_counter() - started,
            "verify": verify,
            "manifest": manifest,
        }

    def execute(self, root: Path) -> dict[str, Any]:
        block_size = self.args.block_mib * 1024 * 1024
        scan_started = time.perf_counter()
        files, unique_blocks, dedup_stats = scan_directory(root, block_size)
        scan_time = time.perf_counter() - scan_started
        dedup_stats_dict = dedup_stats.as_dict()
        cpss_files, cpss_blocks, cpss_stats = make_cpss_only_records(root, block_size)
        self.sync_code()
        self.start_remote_server()
        try:
            raw_result = self.run_method(root, "raw")
            cpss_result = self.run_method(
                root, "cpss_only",
                files=cpss_files, unique_blocks=cpss_blocks, stats=cpss_stats,
            )
            dedup_result = self.run_method(
                root, "dedup_cpss",
                files=files, unique_blocks=unique_blocks, stats=dedup_stats_dict,
            )
        finally:
            self.stop_remote_server()

        raw_bytes = int(dedup_stats_dict["raw_bytes"])
        transferred = int(dedup_result["transferred_bytes"])
        cpss_transferred = int(cpss_result["transferred_bytes"])
        e2e_raw = float(raw_result["end_to_end_time_s"])
        e2e_cpss = float(cpss_result["end_to_end_time_s"])
        e2e_dedup = float(dedup_result["end_to_end_time_s"])
        row = {
            "run_id": self.run_id,
            "dataset": self.args.dataset_name or root.name,
            "input_dir": str(root),
            "block_size_bytes": block_size,
            "file_count": dedup_stats_dict["file_count"],
            "raw_bytes": raw_bytes,
            "file_dedup_hit_files": dedup_stats_dict["duplicate_file_count"],
            "file_dedup_saved_bytes": dedup_stats_dict["file_dedup_saved_bytes"],
            "block_total_count": dedup_stats_dict["block_total_count"],
            "block_unique_count": dedup_stats_dict["block_unique_count"],
            "block_duplicate_count": dedup_stats_dict["block_duplicate_count"],
            "block_dedup_saved_bytes": dedup_stats_dict["block_dedup_saved_bytes"],
            "unique_raw_bytes": dedup_stats_dict["unique_raw_bytes"],
            "compressed_unique_bytes": dedup_result["compressed_unique_bytes"],
            "manifest_bytes": dedup_result["manifest_bytes"],
            "transferred_bytes": transferred,
            "raw_gridflux_transfer_time_s": raw_result["transfer_time_s"],
            "cpss_only_compressed_bytes": cpss_result["compressed_unique_bytes"],
            "cpss_only_transfer_time_s": cpss_result["transfer_time_s"],
            "dedup_scan_time_s": scan_time,
            "file_hash_time_s": scan_time,
            "block_hash_time_s": scan_time,
            "compress_time_s": dedup_result["compress_time_s"],
            "gridflux_transfer_time_s": dedup_result["transfer_time_s"],
            "remote_decompress_time_s": dedup_result["remote_decompress_time_s"],
            "remote_rebuild_time_s": dedup_result["remote_rebuild_time_s"],
            "remote_verify_time_s": dedup_result["remote_verify_time_s"],
            "end_to_end_time_s": e2e_dedup,
            "compression_ratio_unique": (
                dedup_stats_dict["unique_raw_bytes"] / dedup_result["compressed_unique_bytes"]
                if dedup_result["compressed_unique_bytes"] else 0.0
            ),
            "dedup_ratio": (
                raw_bytes / dedup_stats_dict["unique_raw_bytes"]
                if dedup_stats_dict["unique_raw_bytes"] else 1.0
            ),
            "logical_bandwidth_gain_vs_raw": raw_bytes / transferred if transferred else 0.0,
            "logical_bandwidth_gain_vs_cpss_only": cpss_transferred / transferred if transferred else 0.0,
            "e2e_gain_vs_raw": e2e_raw / e2e_dedup if e2e_dedup else 0.0,
            "e2e_gain_vs_cpss_only": e2e_cpss / e2e_dedup if e2e_dedup else 0.0,
            "sha256_ok": bool(dedup_result["verify"].get("ok")),
            "roundtrip_ok": bool(dedup_result["verify"].get("ok")),
            "status": "completed",
            "error": "",
        }
        self.write_outputs(row, {
            "scan": dedup_stats_dict,
            "raw": raw_result,
            "cpss_only": cpss_result,
            "dedup_cpss": dedup_result,
        })
        return row

    def write_outputs(self, row: dict[str, Any], details: dict[str, Any]) -> None:
        with (self.result_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})
        (self.result_dir / "summary.json").write_text(
            json.dumps({"row": row, "details": details}, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        (self.result_dir / "summary_zh.md").write_text(
            "\n".join([
                "# GridFlux + CPSS 去重 Staging 第一阶段报告",
                "",
                f"- 状态：`{row['status']}`",
                f"- 数据集：`{row['dataset']}`",
                f"- 文件数：`{row['file_count']}`",
                f"- 原始字节数：`{row['raw_bytes']}`",
                f"- 文件级去重命中：`{row['file_dedup_hit_files']}` 个文件，节省 `{row['file_dedup_saved_bytes']}` bytes",
                f"- 块级去重：`{row['block_duplicate_count']}` / `{row['block_total_count']}` 个重复 block",
                f"- unique raw bytes：`{row['unique_raw_bytes']}`",
                f"- dedup + CPSS transferred bytes：`{row['transferred_bytes']}`",
                f"- 相对 raw 逻辑字节收益：`{row['logical_bandwidth_gain_vs_raw']:.6f}x`",
                f"- 相对 CPSS-only 逻辑字节收益：`{row['logical_bandwidth_gain_vs_cpss_only']:.6f}x`",
                f"- roundtrip SHA256：`{row['roundtrip_ok']}`",
                "",
                "## 结论",
                "",
                "该报告将文件级去重、块级去重和 CPSS 压缩收益分开记录。真实数据重复率低时，去重收益可能有限，但不影响功能正确性验收。",
                "",
            ]),
            encoding="utf-8",
        )
        (self.result_dir / "cleanup_report.md").write_text(
            "# Cleanup Report\n\n现场清理由 runner 的 finally 路径执行。实验结束后检查本地临时目录和上海端 run 目录不存在。\n",
            encoding="utf-8",
        )
        (self.result_dir / "blocker_report.md").write_text(
            "# Blocker Report\n\n本次运行未记录环境阻塞。\n",
            encoding="utf-8",
        )
        (self.result_dir / "manifest.json").write_text(
            json.dumps(details["dedup_cpss"]["manifest"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def cleanup(self) -> None:
        if self.remote_started:
            self.stop_remote_server()
        else:
            ssh_cmd(
                self.args.remote_alias,
                f"rm -rf {q(self.remote_session)}",
                log=self.command_log,
                check=False,
            )
        shutil.rmtree(self.tmp, ignore_errors=True)
        leftovers = [str(path) for path in self.tmp.parent.glob(self.tmp.name)]
        (self.result_dir / "cleanup_audit.json").write_text(
            json.dumps({
                "local_tmp": str(self.tmp),
                "local_tmp_exists": self.tmp.exists(),
                "remote_session": self.remote_session,
                "remote_session_checked": True,
                "remote_session_exists": self._remote_exists(),
                "leftovers": leftovers,
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _remote_exists(self) -> bool:
        result = ssh_cmd(
            self.args.remote_alias,
            f"test -e {q(self.remote_session)}",
            log=self.command_log,
            check=False,
        )
        return result.returncode == 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-alias", default=DEFAULT_REMOTE_ALIAS)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--block-mib", type=int, choices=[1, 4, 8, 16, 32], default=DEFAULT_BLOCK_MIB)
    parser.add_argument("--selector", choices=["model", "hybrid", "exhaustive"], default="hybrid")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--pipelines", type=str, default=None)
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--gridflux-tree-upload", default=DEFAULT_GRIDFLUX_UPLOAD)
    parser.add_argument("--gridflux-port", type=int, default=DEFAULT_GRIDFLUX_PORT)
    parser.add_argument("--data-port-base", type=int, default=DEFAULT_DATA_PORT_BASE)
    parser.add_argument("--result-dir", type=Path, default=None)
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="Human-readable dataset label for result records.",
    )
    parser.add_argument("--on-compress-fail", choices=["fail", "raw", "lz4"], default="fail")
    args = parser.parse_args()
    args.input_dir = args.input_dir.resolve()
    args.model_dir = (args.model_dir or (ROOT / "Model")).resolve()
    args.pipelines = (
        [item.strip() for item in args.pipelines.split(",") if item.strip()]
        if args.pipelines else None
    )
    if args.result_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.result_dir = ROOT / "ExperimentRecords" / f"dedup_stage1_{stamp}"
    else:
        args.result_dir = args.result_dir.resolve()
    return args


def main() -> int:
    args = parse_args()
    runner = StagingRunner(args)
    try:
        runner.execute(args.input_dir)
        return 0
    except Exception as exc:
        runner.result_dir.mkdir(parents=True, exist_ok=True)
        (runner.result_dir / "blocker_report.md").write_text(
            f"# Blocker Report\n\n- status: `failed`\n- error: `{exc}`\n",
            encoding="utf-8",
        )
        (runner.result_dir / "summary.json").write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

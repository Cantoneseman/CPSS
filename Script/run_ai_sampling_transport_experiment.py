#!/usr/bin/env python3
"""Legacy sampling-sensitivity and GridFlux end-to-end experiment runner.

New local CPSS processing should use Script/run_cpss_pipeline.py. This script is
kept for historical GridFlux transport matrices until the remote control stage is
merged into the unified runner.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import lz4.frame as lz4_frame
except ImportError:  # pragma: no cover
    lz4_frame = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "Script"
GRIDFLUX_ROOT = Path("/root/projects/GridFlux-Beta")
LOCAL_GRIDFLUX_CLIENT = GRIDFLUX_ROOT / "build" / "gridflux-file-client"
REMOTE_ALIAS = "gridflux-beta-shanghai"
REMOTE_HOST = "47.116.174.181"
REMOTE_PROJECT = "/root/projects/science-compressor"
REMOTE_GRIDFLUX_SERVER = "/root/projects/GridFlux/build-cloud/gridflux-file-server"
REMOTE_PYTHON = f"{REMOTE_PROJECT}/.venv/bin/python"
GRIDFLUX_PORT = 25252
BUCKET = "science-compressor-datasets"
OSS_OBJECTS = {
    "openorca": f"oss://{BUCKET}/AI Dataset/model/ai_openorca_1m_gpt4_augmented.tar.zst",
    "slimorca": f"oss://{BUCKET}/AI Dataset/model/ai_slimorca_oo_labeled_correct_gpt4.tar.zst",
    "wikitext": f"oss://{BUCKET}/AI Dataset/model/ai_wikitext103_raw_v1.tar.zst",
    "metadata": f"oss://{BUCKET}/AI Dataset/Metadata_Training/metadata_training_20260727.tar.zst",
}
SAMPLING_METHODS = ("head_sequential", "uniform_random", "position_stratified")
SCENARIOS = ("llm_text", "metadata")
BASE_CANDIDATES = [
    "zstd",
    "zlib",
    "libdeflate",
    "snappy",
    "brotli",
    "bzip2",
    "xz_lzma2",
    "delta_length_byte_array->xz_lzma2",
]
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".txt", ".raw", ".tsv"}
CHUNK_BYTES = 4 * 1024 * 1024
SEED = 20260727
REPEATS = 3
TRAIN_BLOCKS_PER_GROUP = 64
EXTERNAL_BLOCKS_PER_GROUP = 96

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from Compressor.container import build_header, encode_metadata
from Compressor.deploy_registry import DEPLOYABLE_PIPELINES
from Compressor.reversible import CompressionArtifact, compress_with_pipeline
from Compressor.runtime import PipelineContext
from Compressor.selector import (
    SELECTOR_MODE_CODES,
    NNMaxEnsembleSelector,
    artifact_total_size,
    exhaustive_select,
    select_pipeline,
)
from Script.run_ai_scenarios import (
    assign_internal_split,
    find_one,
    metadata_sources,
    model_sources,
    unzip_wikitext,
    write_chunk,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_oss_stat(text: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Content-Length"):
            record["size_bytes"] = int(line.split(":", 1)[1].strip())
        elif line.startswith("Etag"):
            record["etag"] = line.split(":", 1)[1].strip()
        elif line.startswith("Last-Modified"):
            record["last_modified"] = line.split(":", 1)[1].strip()
        elif line.startswith("X-Oss-Hash-Crc64ecma"):
            record["crc64"] = line.split(":", 1)[1].strip()
    return record


def run(command: list[str], *, capture: bool = False, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(shell_quote(item) for item in command), flush=True)
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=capture,
        check=True,
    )


def run_remote(
    command: str,
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print("$ ssh " + REMOTE_ALIAS + " " + command, flush=True)
    return subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            REMOTE_ALIAS,
            command,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=capture,
        check=check,
    )


def discover_extracted_files(extracted: dict[str, Path]) -> dict[str, list[tuple[Path, str]]]:
    openorca, slimorca = model_sources(extracted)
    wiki_root = unzip_wikitext(extracted["wikitext"])
    wiki = [
        (path, "wikitext")
        for path in sorted(wiki_root.rglob("*.raw"))
        if path.is_file()
    ]
    return {
        "llm_train": [(openorca[0], openorca[1]), (slimorca[0], slimorca[1])],
        "llm_external": wiki,
        "metadata_train": metadata_sources(extracted, {"openimages", "laion"}),
        "metadata_external": metadata_sources(extracted, {"embedding"}),
    }


def read_text_chunk(path: Path, offset: int, size: int) -> tuple[int, bytes]:
    with path.open("rb") as handle:
        handle.seek(max(0, offset))
        if offset > 0:
            handle.readline()
        actual_offset = handle.tell()
        payload = handle.read(size)
    return actual_offset, payload


def sample_offsets(
    path: Path,
    method: str,
    count: int,
    *,
    seed: int,
    chunk_bytes: int = CHUNK_BYTES,
) -> list[int]:
    size = path.stat().st_size
    if size <= chunk_bytes:
        return [0]
    max_start = size - chunk_bytes
    candidates: list[int] = []
    if method == "head_sequential":
        candidates = [i * chunk_bytes for i in range(count)]
    elif method == "uniform_random":
        rng = random.Random(seed)
        attempts = 0
        seen: set[int] = set()
        while len(candidates) < count and attempts < count * 30:
            value = rng.randint(0, max_start)
            if path.suffix.lower() in TEXT_SUFFIXES and value > 0:
                with path.open("rb") as handle:
                    handle.seek(value)
                    handle.readline()
                    value = handle.tell()
            if value not in seen:
                seen.add(value)
                candidates.append(value)
            attempts += 1
    elif method == "position_stratified":
        for index in range(count):
            fraction = index / max(1, count - 1)
            candidates.append(int(round(max_start * fraction)))
    else:
        raise ValueError(f"unknown sampling method: {method}")

    normalized: list[int] = []
    seen: set[int] = set()
    for value in candidates:
        value = min(max(0, value), max_start)
        if path.suffix.lower() in TEXT_SUFFIXES and value > 0:
            with path.open("rb") as handle:
                handle.seek(value)
                handle.readline()
                value = handle.tell()
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    return normalized[:count]


def allocate_file_budgets(
    files: list[tuple[Path, str]],
    total_blocks: int,
) -> dict[Path, int]:
    if not files:
        raise RuntimeError("empty source group")
    budget = {path: 0 for path, _group in files}
    for index in range(total_blocks):
        path = files[index % len(files)][0]
        budget[path] += 1
    return budget


def group_source_files(files: list[tuple[Path, str]]) -> dict[str, list[tuple[Path, str]]]:
    grouped: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for path, source_group in files:
        grouped[str(source_group)].append((path, source_group))
    if not grouped:
        raise RuntimeError("empty source groups")
    return dict(sorted(grouped.items()))


def collect_sampled_records(
    output_root: Path,
    files: list[tuple[Path, str]],
    *,
    scenario: str,
    method: str,
    blocks_per_group: int,
    external: bool,
    seed: int,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    next_id = 1
    grouped_files = group_source_files(files)
    for group_index, (source_group, group_files) in enumerate(grouped_files.items()):
        budgets = allocate_file_budgets(group_files, blocks_per_group)
        for file_index, (source_path, _group) in enumerate(group_files):
            count = budgets[source_path]
            offsets = sample_offsets(
                source_path,
                method,
                count,
                seed=seed + group_index * 100_003 + file_index * 1_009,
            )
            for offset_index, offset in enumerate(offsets):
                if source_path.suffix.lower() in TEXT_SUFFIXES:
                    actual_offset, payload = read_text_chunk(source_path, offset, CHUNK_BYTES)
                else:
                    actual_offset = offset
                    with source_path.open("rb") as handle:
                        handle.seek(offset)
                        payload = handle.read(CHUNK_BYTES)
                if not payload:
                    continue
                record = write_chunk(
                    output_root,
                    next_id,
                    payload,
                    source_path=source_path,
                    source_group=source_group,
                    scenario=scenario,
                    offset=actual_offset,
                )
                record.update({
                    "sampling_method": method,
                    "sampling_seed": seed,
                    "sampling_group_index": group_index,
                    "sampling_file_index": file_index,
                    "sampling_offset_index": offset_index,
                    "source_size_bytes": source_path.stat().st_size,
                    "split": "external" if external else "train",
                })
                records.append(record)
                next_id += 1
    if not records:
        raise RuntimeError(f"no records generated for {scenario}/{method}")
    if not external:
        assign_internal_split(records, seed)
    metadata = {
        "scenario": scenario,
        "sampling_method": method,
        "sampling_seed": seed,
        "external": external,
        "chunk_bytes": CHUNK_BYTES,
        "requested_blocks_per_group": blocks_per_group,
        "source_group_count": len(grouped_files),
        "record_count": len(records),
        "source_groups": sorted({str(row["source_group"]) for row in records}),
    }
    data = {"_meta": metadata}
    data.update({str(row["id"]): row for row in records})
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return records


def available_candidates(sample: bytes) -> tuple[list[str], dict[str, str]]:
    context = PipelineContext(
        dtype_name=None,
        shape=(len(sample),),
        typesize=1,
        metadata={"scenario": "llm_text", "format": "jsonl"},
    )
    metadata = {
        "dtype_name": None,
        "shape": [len(sample)],
        "typesize": 1,
        "endian": "little",
        "shape_source": "byte_fallback",
        "original_suffix": ".jsonl",
        "scenario": "llm_text",
        "format": "jsonl",
    }
    names = {row.name for row in DEPLOYABLE_PIPELINES}
    available: list[str] = []
    failures: dict[str, str] = {}
    for name in BASE_CANDIDATES:
        if name not in names:
            continue
        try:
            artifact = compress_with_pipeline(
                sample,
                name,
                context,
                context_metadata=metadata,
                selector_metadata={"mode": "preflight"},
            )
            from Compressor.reversible import decompress_from_pipeline_id
            if decompress_from_pipeline_id(
                artifact.payload,
                artifact.pipeline_id,
                artifact.metadata,
                artifact.tail,
            ) != sample:
                raise RuntimeError("roundtrip mismatch")
            available.append(name)
        except Exception as exc:
            failures[name] = str(exc)
    return available, failures


def write_code_manifest(root: Path, output: Path) -> None:
    ignored = {".venv", "__pycache__", ".pytest_cache", "ExperimentRecords", "Dataset"}
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in ignored for part in relative.parts):
            continue
        if path.suffix in {".pt", ".npz"} and "Model" not in relative.parts:
            continue
        rows.append({
            "path": str(relative),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        })
    output.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")


def sync_code_to_shanghai() -> dict[str, Any]:
    remote_root = REMOTE_PROJECT
    run([
        "rsync",
        "-az",
        "--exclude=.venv/",
        "--exclude=__pycache__/",
        "--exclude=.pytest_cache/",
        "--exclude=Dataset/",
        "--exclude=ExperimentRecords/",
        "--exclude=*.part",
        "--exclude=*.lock",
        str(ROOT) + "/",
        f"{REMOTE_ALIAS}:{remote_root}/",
    ])
    with tempfile.NamedTemporaryFile(
        prefix="science-compressor-code-manifest-",
        suffix=".json",
        dir="/tmp",
        delete=False,
    ) as handle:
        local_manifest = Path(handle.name)
    remote_manifest = "/tmp/science-compressor-code-manifest.json"
    try:
        write_code_manifest(ROOT, local_manifest)
        run([
            "rsync",
            "-az",
            str(local_manifest),
            f"{REMOTE_ALIAS}:{remote_manifest}",
        ])
        remote_check = run_remote(
            f"cd {shell_quote(remote_root)} && python3 - <<'PY'\n"
            "import hashlib, json\n"
            "from pathlib import Path\n"
            "root=Path('.')\n"
            "expected=json.loads(Path('/tmp/science-compressor-code-manifest.json').read_text())\n"
            "actual=[]\n"
            "ignored={'.venv','__pycache__','.pytest_cache','ExperimentRecords','Dataset'}\n"
            "for p in sorted(root.rglob('*')):\n"
            "    if not p.is_file(): continue\n"
            "    r=p.relative_to(root)\n"
            "    if any(x in ignored for x in r.parts): continue\n"
            "    if p.suffix in {'.pt','.npz'} and 'Model' not in r.parts: continue\n"
            "    h=hashlib.sha256(p.read_bytes()).hexdigest()\n"
            "    actual.append({'path':str(r),'size_bytes':p.stat().st_size,'sha256':h})\n"
            "actual_map={row['path']: row for row in actual}\n"
            "expected_map={row['path']: row for row in expected}\n"
            "match=all(actual_map.get(path)==row for path,row in expected_map.items())\n"
            "print(json.dumps({'match': match, 'local_count': len(expected), 'remote_count': len(actual), 'missing': sorted(set(expected_map)-set(actual_map))}))\n"
            "PY",
            capture=True,
        )
        return json.loads(remote_check.stdout.strip().splitlines()[-1])
    finally:
        local_manifest.unlink(missing_ok=True)
        run_remote(
            f"unlink {shell_quote(remote_manifest)} 2>/dev/null || true",
            capture=True,
        )


def setup_remote_environment() -> dict[str, Any]:
    run_remote(
        "mkdir -p /root/projects/GridFlux/build-cloud /root/projects/science-compressor/.venv"
    )
    run([
        "rsync",
        "-az",
        str(LOCAL_GRIDFLUX_CLIENT),
        f"{REMOTE_ALIAS}:/root/projects/GridFlux/build-cloud/gridflux-file-client",
    ])
    run([
        "rsync",
        "-az",
        str(GRIDFLUX_ROOT / "build" / "gridflux-file-server"),
        f"{REMOTE_ALIAS}:{REMOTE_GRIDFLUX_SERVER}",
    ])
    remote = run_remote(
        "set -e; "
        "if [ ! -x /root/projects/science-compressor/.venv/bin/python ]; then "
        "python3 -m venv /root/projects/science-compressor/.venv; fi; "
        "/root/projects/science-compressor/.venv/bin/pip install -q --disable-pip-version-check "
        "numpy==2.2.6 lz4==4.3.3 zstandard==0.25.0 brotli==1.2.0 python-snappy==0.7.3; "
        "chmod +x /root/projects/GridFlux/build-cloud/gridflux-file-server; "
        "test -x /root/projects/GridFlux/build-cloud/gridflux-file-server; "
        "/root/projects/science-compressor/.venv/bin/python --version",
        capture=True,
    )
    return {
        "remote_python": remote.stdout.strip().splitlines()[-1] if remote.stdout else "",
        "server": REMOTE_GRIDFLUX_SERVER,
        "port": GRIDFLUX_PORT,
    }


def build_cpss_blob(
    raw: bytes,
    selection: Any,
) -> bytes:
    metadata_blob = encode_metadata(selection.artifact.metadata)
    header = build_header(
        selector_mode=SELECTOR_MODE_CODES.get(selection.selector_mode, 0),
        pipeline_id=selection.artifact.pipeline_id,
        metadata_size=len(metadata_blob),
        tail_size=len(selection.artifact.tail),
        payload_size=len(selection.artifact.payload),
        original_size=len(raw),
    )
    return header + metadata_blob + selection.artifact.tail + selection.artifact.payload


def file_record(path: Path, root: Path, scenario: str, method: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "sampling_method": method,
        "source_file": str(path),
        "slice_file": str(path.relative_to(root)),
        "original_size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def compression_payload(
    raw: bytes,
    record: dict[str, Any],
    *,
    method: str,
    model_dir: Path | None,
    candidates: list[str],
    selector_cache: dict[Path, NNMaxEnsembleSelector] | None = None,
) -> tuple[bytes, dict[str, float], str]:
    context = PipelineContext(
        dtype_name=None,
        shape=(len(raw),),
        typesize=1,
        metadata={
            "scenario": record["scenario"],
            "format": record.get("format", ""),
            "source_group": record.get("source_group", ""),
        },
    )
    context_metadata = {
        "dtype_name": None,
        "shape": [len(raw)],
        "typesize": 1,
        "endian": "little",
        "shape_source": "byte_fallback",
        "original_suffix": record.get("original_suffix", ""),
        "scenario": record["scenario"],
        "format": record.get("format", ""),
        "source_group": record.get("source_group", ""),
        "element_count": len(raw),
        "aligned_size": len(raw),
    }
    if method == "raw":
        return raw, {"feature_time_sec": 0.0, "inference_time_sec": 0.0, "compress_time_sec": 0.0}, "raw"
    if method == "gzip":
        started = time.perf_counter()
        blob = gzip.compress(raw, compresslevel=6, mtime=0)
        return blob, {
            "feature_time_sec": 0.0,
            "inference_time_sec": 0.0,
            "compress_time_sec": time.perf_counter() - started,
        }, "gzip"
    if method == "lz4":
        if lz4_frame is None:
            raise RuntimeError("lz4.frame is not installed")
        started = time.perf_counter()
        blob = lz4_frame.compress(raw, compression_level=0)
        return blob, {
            "feature_time_sec": 0.0,
            "inference_time_sec": 0.0,
            "compress_time_sec": time.perf_counter() - started,
        }, "lz4"

    if method == "exhaustive":
        started = time.perf_counter()
        selection = exhaustive_select(raw, context, context_metadata, candidates)
        blob = build_cpss_blob(raw, selection)
        return blob, {
            "feature_time_sec": 0.0,
            "inference_time_sec": 0.0,
            "compress_time_sec": time.perf_counter() - started,
        }, selection.artifact.pipeline_name

    if model_dir is None:
        raise ValueError(f"model_dir required for {method}")
    if selector_cache is not None and model_dir in selector_cache:
        selector = selector_cache[model_dir]
    else:
        selector = NNMaxEnsembleSelector(model_dir=model_dir)
        if selector_cache is not None:
            selector_cache[model_dir] = selector
    feature_started = time.perf_counter()
    tensor = selector._tensor_features(raw, context, context_metadata)
    feature_time = time.perf_counter() - feature_started
    inference_started = time.perf_counter()
    ranking = selector.rank_tensor(tensor, set(candidates))
    inference_time = time.perf_counter() - inference_started
    picked = next(row for row in ranking if row["available"])
    compress_started = time.perf_counter()
    artifact = compress_with_pipeline(
        raw,
        picked["pipeline"],
        context,
        context_metadata=context_metadata,
        selector_metadata={"mode": "model", "top_prediction": picked["pipeline"]},
    )
    blob = build_cpss_blob(raw, type("Selection", (), {
        "selector_mode": "model",
        "artifact": artifact,
    })())
    return blob, {
        "feature_time_sec": feature_time,
        "inference_time_sec": inference_time,
        "compress_time_sec": time.perf_counter() - compress_started,
    }, picked["pipeline"]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(math.ceil((p / 100.0) * len(values))) - 1))
    return values[index]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def launch_remote_server(
    remote_input: str,
    remote_event: str,
    tag: str,
    *,
    port: int = GRIDFLUX_PORT,
) -> dict[str, str]:
    remote_output = remote_input
    stdout_path = f"{remote_input}.stdout"
    stderr_path = f"{remote_input}.stderr"
    exit_path = f"{remote_input}.exit"
    pid_path = f"{remote_input}.pid"
    server_command = (
        f"{shell_quote(REMOTE_GRIDFLUX_SERVER)} --host 0.0.0.0 --port {port} "
        f"--output {shell_quote(remote_output)} --connections 4 --buffer-size 1048576 "
        f"--checksum crc32c --checksum-backend auto "
        f"--event-log {shell_quote(remote_event)} --overwrite; "
        f"status=$?; printf '%s\\n' \"$status\" > {shell_quote(exit_path)}"
    )
    command = (
        f"for p in {shell_quote(remote_output)} {shell_quote(remote_event)} "
        f"{shell_quote(stdout_path)} {shell_quote(stderr_path)} {shell_quote(exit_path)} {shell_quote(pid_path)}; "
        f"do unlink \"$p\" 2>/dev/null || true; done; "
        f"nohup sh -c {shell_quote(server_command)} </dev/null "
        f"> {shell_quote(stdout_path)} 2> {shell_quote(stderr_path)} & "
        f"printf '%s\\n' \"$!\" > {shell_quote(pid_path)}"
    )
    run_remote(command, capture=True)
    return {
        "output": remote_output,
        "event": remote_event,
        "stdout": stdout_path,
        "stderr": stderr_path,
        "exit": exit_path,
        "pid": pid_path,
    }


def wait_remote_exit(exit_path: str, timeout_sec: float = 3600.0) -> int | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        result = run_remote(
            f"test -f {shell_quote(exit_path)} && cat {shell_quote(exit_path)} || true",
            capture=True,
        )
        lines = result.stdout.strip().splitlines()
        if lines:
            try:
                return int(lines[-1])
            except ValueError:
                return None
        time.sleep(0.5)
    return None


def gridflux_transfer(
    local_input: Path,
    remote_input: str,
    log_path: Path,
    run_tag: str,
    *,
    port: int = GRIDFLUX_PORT,
) -> tuple[float, str]:
    remote_event = f"{remote_input}.events.jsonl"
    paths = launch_remote_server(remote_input, remote_event, run_tag, port=port)
    deadline = time.time() + 30
    while time.time() < deadline:
        check_result = run_remote(
            f"ss -ltn | grep -q ':{port} '",
            capture=True,
            check=False,
        )
        if check_result.returncode == 0:
            break
        time.sleep(0.4)
    started = time.perf_counter()
    client = subprocess.run(
        [
            str(LOCAL_GRIDFLUX_CLIENT),
            "--host",
            REMOTE_HOST,
            "--port",
            str(port),
            "--input",
            str(local_input),
            "--connections",
            "4",
            "--chunk-size",
            "1048576",
            "--buffer-size",
            "1048576",
            "--checksum",
            "crc32c",
            "--checksum-backend",
            "auto",
            "--event-log",
            str(log_path),
            "--data-final-status-timeout-seconds",
            "30",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    elapsed = time.perf_counter() - started
    server_code = wait_remote_exit(paths["exit"], timeout_sec=60.0)
    error = ""
    if client.returncode != 0:
        error = client.stderr or client.stdout or f"GridFlux client code {client.returncode}"
    elif server_code is None:
        error = "GridFlux server did not exit within 60 seconds"
    elif server_code != 0:
        error = f"GridFlux server code {server_code}"
    return elapsed, error


def cleanup_remote(paths: Iterable[str]) -> None:
    values = " ".join(shell_quote(path) for path in paths)
    if values:
        run_remote(
            f"for p in {values}; do unlink \"$p\" 2>/dev/null || true; done",
            capture=True,
        )


def ensure_local_venv(path: Path) -> str:
    if not (path / "bin" / "python").exists():
        run([sys.executable, "-m", "venv", str(path)])
    python = str(path / "bin" / "python")
    run([
        python,
        "-m",
        "pip",
        "install",
        "-q",
        "--disable-pip-version-check",
        "numpy==2.2.6",
        "torch==2.13.0",
        "zstandard==0.25.0",
        "brotli==1.2.0",
        "lz4==4.3.3",
        "python-snappy==0.7.3",
        "pytest==9.1.1",
    ])
    return python


def train_scenario_model(
    python: str,
    dataset_root: Path,
    model_dir: Path,
    scenario: str,
    candidates: list[str],
) -> dict[str, Any]:
    env = os.environ.copy()
    env["PIPELINES"] = ",".join(candidates)
    env["COMPRESS_LABEL_SPLIT_SEED"] = str(SEED)
    subprocess.run(
        [
            python,
            str(SCRIPT_DIR / "compress_and_label.py"),
            "--dataset-root",
            str(dataset_root),
            "--scenario",
            scenario,
            "--workers",
            "1",
            "--progress-every",
            "50",
            "--slice-pipeline-progress-every",
            "0",
        ],
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    subprocess.run(
        [
            python,
            str(SCRIPT_DIR / "mian_train_and_test.py"),
            "--dataset-root",
            str(dataset_root),
            "--scenario",
            scenario,
            "--model-dir",
            str(model_dir),
            "--cpu",
            "--epochs",
            "50",
            "--eval-every",
            "10",
            "--max-seeds",
            "2",
            "--seed-list",
            "1,2",
            "--top-k",
            "3",
            "--num-thresholds",
            "7",
        ],
        cwd=str(ROOT),
        env=env,
        check=True,
    )
    report = model_dir / "test_report_nnmax.json"
    return json.loads(report.read_text(encoding="utf-8"))


def run_local_metrics(
    run_root: Path,
    external_roots: dict[tuple[str, str], Path],
    model_dirs: dict[tuple[str, str], Path],
    candidates: list[str],
    python: str,
    *,
    include_exhaustive: bool = True,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    staging = run_root / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    selector_cache: dict[Path, NNMaxEnsembleSelector] = {}
    for (scenario, external_method), dataset_root in external_roots.items():
        data = json.loads((dataset_root / "data.json").read_text(encoding="utf-8"))
        records = [row for key, row in data.items() if key != "_meta"]
        for record in records:
            raw_path = dataset_root / record["slice_file"]
            raw = raw_path.read_bytes()
            model_methods = [
                ("model_head_sequential", model_dirs[(scenario, "head_sequential")]),
                ("model_uniform_random", model_dirs[(scenario, "uniform_random")]),
                ("model_position_stratified", model_dirs[(scenario, "position_stratified")]),
            ]
            methods: list[tuple[str, Path | None]] = [
                ("raw", None),
                ("gzip", None),
                ("lz4", None),
                *model_methods,
            ]
            if include_exhaustive:
                methods.append(("exhaustive", None))
            for method, model_dir in methods:
                for repeat in range(1, REPEATS + 1):
                    if method.startswith("model_"):
                        payload, times, selected_pipeline = compression_payload(
                            raw,
                            record,
                            method=method,
                            model_dir=model_dir,
                            candidates=candidates,
                            selector_cache=selector_cache,
                        )
                    else:
                        payload, times, selected_pipeline = compression_payload(
                            raw,
                            record,
                            method=method,
                            model_dir=None,
                            candidates=candidates,
                        )
                    stage_path = staging / (
                        f"{scenario}_{external_method}_{record['id']}_{method}_rep{repeat}.bin"
                    )
                    stage_path.write_bytes(payload)
                    rows.append({
                        "scenario": scenario,
                        "external_sampling_method": external_method,
                        "training_model": method.removeprefix("model_") if method.startswith("model_") else method,
                        "block_id": record["id"],
                        "source_group": record.get("source_group", ""),
                        "source_file": record.get("source_file", ""),
                        "repeat": repeat,
                        "original_size_bytes": len(raw),
                        "compressed_size_bytes": len(payload),
                        "compression_ratio": len(raw) / max(1, len(payload)),
                        "feature_time_sec": times["feature_time_sec"],
                        "inference_time_sec": times["inference_time_sec"],
                        "compress_time_sec": times["compress_time_sec"],
                        "selected_pipeline": selected_pipeline,
                        "stage_path": str(stage_path),
                        "sha256_local": sha256_bytes(raw),
                        "status": "ready",
                    })
    return rows


def execute_transport(
    run_root: Path,
    rows: list[dict[str, Any]],
) -> None:
    logs = run_root / "gridflux_logs"
    logs.mkdir(parents=True, exist_ok=True)
    remote_root = f"/tmp/science-compressor-ai-transport-{run_root.name}"
    run_remote(
        f"mkdir -p {shell_quote(remote_root)}; "
        f"find {shell_quote(remote_root)} -mindepth 1 -maxdepth 1 -type f -delete"
    )
    for index, row in enumerate(rows, start=1):
        stage_path = Path(row["stage_path"])
        remote_input = f"{remote_root}/{stage_path.name}"
        remote_restored = f"{remote_input}.restored"
        method = (
            "cpss"
            if row["training_model"] not in {"raw", "gzip", "lz4"}
            else row["training_model"]
        )
        transfer_time, transfer_error = gridflux_transfer(
            stage_path,
            remote_input,
            logs / f"{index:06d}.client.jsonl",
            f"{row['scenario']}_{row['block_id']}_{row['training_model']}_rep{row['repeat']}",
        )
        row["transfer_time_sec"] = transfer_time
        row["logical_bandwidth_mib_s"] = (
            row["original_size_bytes"] / transfer_time / 1048576
            if transfer_time > 0 else 0.0
        )
        if transfer_error:
            row.update({
                "status": "failed",
                "error": transfer_error,
                "roundtrip_pass": False,
            })
            cleanup_remote([remote_input, remote_restored, f"{remote_input}.*"])
            continue
        restore_command = (
            f"cd {shell_quote(REMOTE_PROJECT)} && "
            f"{shell_quote(REMOTE_PYTHON)} {shell_quote(str(Path(SCRIPT_DIR.name) / 'remote_restore.py'))} "
            f"{shell_quote(method)} {shell_quote(remote_input)} {shell_quote(remote_restored)}"
        )
        restore = run_remote(restore_command, capture=True, check=False)
        if restore.returncode != 0:
            row.update({
                "status": "failed",
                "error": restore.stderr or restore.stdout,
                "roundtrip_pass": False,
            })
            cleanup_remote([remote_input, remote_restored])
            continue
        result = json.loads(restore.stdout.strip().splitlines()[-1])
        row["remote_decompress_time_sec"] = result["restore_time_sec"]
        row["restored_size_bytes"] = result["restored_size_bytes"]
        row["sha256_remote"] = result["sha256"]
        row["roundtrip_pass"] = row["sha256_remote"] == row["sha256_local"]
        row["end_to_end_time_sec"] = (
            row["feature_time_sec"]
            + row["inference_time_sec"]
            + row["compress_time_sec"]
            + row["transfer_time_sec"]
            + row["remote_decompress_time_sec"]
        )
        row["end_to_end_logical_bandwidth_mib_s"] = (
            row["original_size_bytes"] / row["end_to_end_time_sec"] / 1048576
            if row["end_to_end_time_sec"] > 0 else 0.0
        )
        row["status"] = "ok" if row["roundtrip_pass"] else "failed"
        row["error"] = "" if row["roundtrip_pass"] else "sha256 mismatch"
        cleanup_remote([remote_input, remote_restored])
        if index % 25 == 0 or index == len(rows):
            print(f"transport {index}/{len(rows)}", flush=True)
    run_remote(
        f"if [ -d {shell_quote(remote_root)} ]; then "
        f"find {shell_quote(remote_root)} -mindepth 1 -maxdepth 1 -type f -delete; "
        f"rmdir {shell_quote(remote_root)} 2>/dev/null || true; fi",
        capture=True,
    )


def execute_transport_parallel(
    run_root: Path,
    rows: list[dict[str, Any]],
    *,
    ports: tuple[int, ...],
) -> None:
    """Run independent GridFlux block transfers concurrently on separate ports."""
    if not ports:
        raise ValueError("at least one GridFlux port is required")
    logs = run_root / "gridflux_logs"
    logs.mkdir(parents=True, exist_ok=True)
    remote_root = f"/tmp/science-compressor-ai-transport-{run_root.name}"
    run_remote(
        f"mkdir -p {shell_quote(remote_root)}; "
        f"find {shell_quote(remote_root)} -mindepth 1 -maxdepth 1 -type f -delete"
    )

    def process(item: tuple[int, dict[str, Any]]) -> int:
        index, row = item
        stage_path = Path(row["stage_path"])
        remote_input = f"{remote_root}/{stage_path.name}"
        remote_restored = f"{remote_input}.restored"
        method = (
            "cpss"
            if row["training_model"] not in {"raw", "gzip", "lz4"}
            else row["training_model"]
        )
        port = ports[(index - 1) % len(ports)]
        transfer_time, transfer_error = gridflux_transfer(
            stage_path,
            remote_input,
            logs / f"{index:06d}.client.jsonl",
            f"{row['scenario']}_{row['block_id']}_{row['training_model']}_rep{row['repeat']}",
            port=port,
        )
        row["transfer_time_sec"] = transfer_time
        row["logical_bandwidth_mib_s"] = (
            row["original_size_bytes"] / transfer_time / 1048576
            if transfer_time > 0 else 0.0
        )
        if transfer_error:
            row.update({
                "status": "failed",
                "error": transfer_error,
                "roundtrip_pass": False,
            })
            return index
        restore_command = (
            f"cd {shell_quote(REMOTE_PROJECT)} && "
            f"{shell_quote(REMOTE_PYTHON)} {shell_quote(str(Path(SCRIPT_DIR.name) / 'remote_restore.py'))} "
            f"{shell_quote(method)} {shell_quote(remote_input)} {shell_quote(remote_restored)}"
        )
        restore = run_remote(restore_command, capture=True, check=False)
        if restore.returncode != 0:
            row.update({
                "status": "failed",
                "error": restore.stderr or restore.stdout or "remote restore failed",
                "roundtrip_pass": False,
            })
            return index
        try:
            result = json.loads(restore.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            row.update({
                "status": "failed",
                "error": f"invalid remote restore output: {exc}",
                "roundtrip_pass": False,
            })
            return index
        row["remote_decompress_time_sec"] = result["restore_time_sec"]
        row["restored_size_bytes"] = result["restored_size_bytes"]
        row["sha256_remote"] = result["sha256"]
        row["roundtrip_pass"] = row["sha256_remote"] == row["sha256_local"]
        row["end_to_end_time_sec"] = (
            row["feature_time_sec"]
            + row["inference_time_sec"]
            + row["compress_time_sec"]
            + row["transfer_time_sec"]
            + row["remote_decompress_time_sec"]
        )
        row["end_to_end_logical_bandwidth_mib_s"] = (
            row["original_size_bytes"] / row["end_to_end_time_sec"] / 1048576
            if row["end_to_end_time_sec"] > 0 else 0.0
        )
        row["status"] = "ok" if row["roundtrip_pass"] else "failed"
        row["error"] = "" if row["roundtrip_pass"] else "sha256 mismatch"
        return index

    completed = 0
    with ThreadPoolExecutor(max_workers=len(ports)) as executor:
        futures = [executor.submit(process, item) for item in enumerate(rows, start=1)]
        for future in as_completed(futures):
            future.result()
            completed += 1
            if completed % 25 == 0 or completed == len(rows):
                print(f"transport {completed}/{len(rows)}", flush=True)
    run_remote(
        f"if [ -d {shell_quote(remote_root)} ]; then "
        f"find {shell_quote(remote_root)} -mindepth 1 -maxdepth 1 -type f -delete; "
        f"rmdir {shell_quote(remote_root)} 2>/dev/null || true; fi",
        capture=True,
    )


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(
            row["scenario"],
            row["external_sampling_method"],
            row["training_model"],
        )].append(row)
    output: list[dict[str, Any]] = []
    for (scenario, external_method, training_model), values in sorted(groups.items()):
        valid = [row for row in values if row.get("status") == "ok"]
        def stat(key: str) -> dict[str, float | None]:
            numbers = [float(row[key]) for row in valid if row.get(key) is not None]
            return {
                "median": percentile(numbers, 50),
                "p95": percentile(numbers, 95),
                "min": min(numbers) if numbers else None,
                "max": max(numbers) if numbers else None,
            }
        output.append({
            "scenario": scenario,
            "external_sampling_method": external_method,
            "training_model": training_model,
            "rows": len(values),
            "ok_rows": len(valid),
            "lossless_rate": sum(bool(row.get("roundtrip_pass")) for row in values) / max(1, len(values)),
            "compression_ratio": stat("compression_ratio"),
            "compress_time_sec": stat("compress_time_sec"),
            "transfer_time_sec": stat("transfer_time_sec"),
            "end_to_end_time_sec": stat("end_to_end_time_sec"),
            "logical_bandwidth_mib_s": stat("logical_bandwidth_mib_s"),
            "end_to_end_logical_bandwidth_mib_s": stat("end_to_end_logical_bandwidth_mib_s"),
            "selected_pipelines": dict(Counter(row.get("selected_pipeline", "") for row in values)),
        })
    return output


def flatten_summaries(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary in summaries:
        rows.append({
            "scenario": summary["scenario"],
            "external_sampling_method": summary["external_sampling_method"],
            "training_model": summary["training_model"],
            "rows": summary["rows"],
            "ok_rows": summary["ok_rows"],
            "lossless_rate": summary["lossless_rate"],
            "compression_ratio_median": summary["compression_ratio"]["median"],
            "compress_time_median_sec": summary["compress_time_sec"]["median"],
            "transfer_time_median_sec": summary["transfer_time_sec"]["median"],
            "end_to_end_time_median_sec": summary["end_to_end_time_sec"]["median"],
            "logical_bandwidth_median_mib_s": summary["logical_bandwidth_mib_s"]["median"],
            "end_to_end_logical_bandwidth_median_mib_s": summary[
                "end_to_end_logical_bandwidth_mib_s"
            ]["median"],
            "selected_pipelines": json.dumps(
                summary["selected_pipelines"],
                ensure_ascii=False,
                sort_keys=True,
            ),
        })
    return rows


def write_report(
    run_root: Path,
    *,
    status: str,
    blocker: str,
    object_info: dict[str, Any],
    candidate_info: dict[str, Any],
    train_reports: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    sampling_manifest: list[dict[str, Any]],
    cleanup: dict[str, Any],
) -> None:
    report = run_root / "final_summary_zh.md"
    lines = [
        "# OSS 采样方法与端到端压缩实验报告",
        "",
        f"- status: `{status}`",
        f"- created_at: `{now()}`",
        "- transfer channel: `GridFlux public TCP/25252`",
        "- Shanghai SSH: `gridflux-beta-shanghai / 47.116.174.181`",
        "",
        "## 实验目标",
        "",
        "验证采样方法对压缩比、压缩时间、端到端时间和逻辑带宽的影响，并比较 AI 场景模型与 gzip/lz4。",
        "",
        "## 状态",
        "",
        f"- blocker: `{blocker or 'none'}`",
        f"- OSS objects inspected: `{len(object_info)}`",
        f"- candidate pipelines: `{candidate_info.get('available', [])}`",
        "",
        "## 采样方法",
        "",
        "- `head_sequential`: 每个文件从头部连续取 4 MiB 块。",
        "- `uniform_random`: 固定种子 `20260727` 在全文件范围随机取块，文本调整到行边界。",
        "- `position_stratified`: 按头部、前中部、中部、后中部、尾部均匀覆盖。",
        "",
        "## 样本清单",
        "",
        "| scenario | sampling | blocks | bytes |",
        "|---|---|---:|---:|",
    ]
    by_sampling: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in sampling_manifest:
        by_sampling[(row["scenario"], row["sampling_method"])].append(row)
    for key, values in sorted(by_sampling.items()):
        lines.append(
            f"| `{key[0]}` | `{key[1]}` | {len(values)} | "
            f"{sum(int(row['chunk_size_bytes']) for row in values)} |"
        )
    lines.extend([
        "",
        "## 训练摘要",
        "",
        "| scenario | sampling | train | test | label_count | labels |",
        "|---|---|---:|---:|---:|---|",
    ])
    single_class_warnings: list[str] = []
    for row in train_reports:
        labels = list(row["report"].get("labels", []))
        label_count = len(labels)
        lines.append(
            f"| `{row['scenario']}` | `{row['sampling_method']}` | "
            f"{row['report'].get('num_train', 'n/a')} | {row['report'].get('num_test', 'n/a')} | "
            f"{label_count} | `{labels}` |"
        )
        if label_count <= 1:
            single_class_warnings.append(
                f"`{row['scenario']}/{row['sampling_method']}` label_count={label_count}"
            )
    if single_class_warnings:
        lines.extend([
            "",
            "## 训练风险",
            "",
            "- 以下模型训练标签只有 1 类，训练准确率不能被解读为具有选择器泛化能力：",
        ])
        for warning in single_class_warnings:
            lines.append(f"- {warning}")
    lines.extend(["", "## 汇总结果", ""])
    lines.extend([
        "| scenario | external sampling | model | compression ratio median | transfer MiB/s median | e2e MiB/s median | e2e time p95 | lossless |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in summaries:
        ratio = row["compression_ratio"]["median"]
        transfer = row["logical_bandwidth_mib_s"]["median"]
        e2e_bw = row["end_to_end_logical_bandwidth_mib_s"]["median"]
        e2e_p95 = row["end_to_end_time_sec"]["p95"]
        lines.append(
            f"| `{row['scenario']}` | `{row['external_sampling_method']}` | `{row['training_model']}` | "
            f"{ratio if ratio is not None else 'n/a'} | {transfer if transfer is not None else 'n/a'} | "
            f"{e2e_bw if e2e_bw is not None else 'n/a'} | {e2e_p95 if e2e_p95 is not None else 'n/a'} | "
            f"{row['lossless_rate']:.4f} |"
        )
    lines.extend([
        "",
        "## 判定口径",
        "",
        "- 采样差异超过 5% 标记为明显影响；低于 5% 标记为当前样本下无明显影响。",
        "- 端到端优势必须同时满足无损恢复，并且端到端逻辑带宽高于对照。",
        "- 仅压缩比更高但端到端更慢时，标记为“压缩率有改善，端到端无改善”。",
        "",
        "## 清理",
        "",
        f"- cleanup: `{cleanup.get('status', 'unknown')}`",
        f"- blocker: `{blocker or 'none'}`",
        "- OSS 原始对象不删除。",
    ])
    if blocker:
        lines.extend(["", "## 阻塞说明", "", f"- `{blocker}`"])
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--python", default=None)
    parser.add_argument("--chunk-bytes", type=int, default=CHUNK_BYTES)
    parser.add_argument("--train-blocks-per-group", type=int, default=TRAIN_BLOCKS_PER_GROUP)
    parser.add_argument("--external-blocks-per-group", type=int, default=EXTERNAL_BLOCKS_PER_GROUP)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--archive-cache", type=Path, default=None)
    parser.add_argument("--local-venv", type=Path, default=Path("/tmp/science-compressor-ai-sampling-venv"))
    parser.add_argument("--skip-transport", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global CHUNK_BYTES, REPEATS
    CHUNK_BYTES = int(args.chunk_bytes)
    REPEATS = int(args.repeats)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_root = (args.run_root or ROOT / "ExperimentRecords" / f"ai_sampling_transport_{run_id}").resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    archives = run_root / "archives"
    extracted_root = run_root / "extracted"
    datasets_root = run_root / "datasets"
    local_venv = args.local_venv.resolve()
    status = "environment_blocked"
    blocker = ""
    object_info: dict[str, Any] = {}
    candidate_info: dict[str, Any] = {"available": [], "failures": {}}
    sampling_manifest: list[dict[str, Any]] = []
    train_reports: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    cleanup = {"status": "not_started"}
    try:
        if not LOCAL_GRIDFLUX_CLIENT.exists():
            raise RuntimeError(f"local GridFlux client missing: {LOCAL_GRIDFLUX_CLIENT}")
        run_remote("hostname && test -d /root/projects/science-compressor", capture=True)
        python = args.python or ensure_local_venv(local_venv)
        archives.mkdir(parents=True, exist_ok=True)
        for name, uri in OSS_OBJECTS.items():
            destination = (
                args.archive_cache / Path(uri).name
                if args.archive_cache
                else archives / Path(uri).name
            )
            if args.skip_download and destination.exists():
                path = destination
            else:
                stat = run(["ossutil", "stat", uri], capture=True)
                run(["ossutil", "cp", uri, str(destination)])
                run(["zstd", "-t", str(destination)])
                path = destination
                object_info[name] = {
                    "uri": uri,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "oss_stat": parse_oss_stat(stat.stdout),
                    "stat_raw": stat.stdout,
                }
            if name not in object_info:
                object_info[name] = {
                    "uri": uri,
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
        extracted: dict[str, Path] = {}
        for name, archive in (
            (key, (args.archive_cache / Path(uri).name if args.archive_cache else archives / Path(uri).name))
            for key, uri in OSS_OBJECTS.items()
        ):
            target = extracted_root / name
            target.mkdir(parents=True, exist_ok=True)
            run(["tar", "--zstd", "-xf", str(archive), "-C", str(target)])
            extracted[name] = target
        sources = discover_extracted_files(extracted)
        sample = next((path.read_bytes()[: CHUNK_BYTES] for path, _group in sources["llm_train"]), None)
        if not sample:
            raise RuntimeError("no LLM training sample found")
        candidate_info["available"], candidate_info["failures"] = available_candidates(sample)
        if len(candidate_info["available"]) < 3:
            raise RuntimeError(f"insufficient candidates: {candidate_info['available']}")
        sync_result = sync_code_to_shanghai()
        if not sync_result.get("match"):
            raise RuntimeError(f"code manifest mismatch: {sync_result}")
        setup_remote_environment()
        model_dirs: dict[tuple[str, str], Path] = {}
        for scenario, source_key in (("llm_text", "llm_train"), ("metadata", "metadata_train")):
            for method in SAMPLING_METHODS:
                dataset_root = datasets_root / scenario / method / "train"
                records = collect_sampled_records(
                    dataset_root,
                    sources[source_key],
                    scenario=scenario,
                    method=method,
                    blocks_per_group=int(args.train_blocks_per_group),
                    external=False,
                    seed=SEED,
                )
                sampling_manifest.extend(records)
                model_dir = ROOT / "Model" / "scenarios" / scenario / method
                if model_dir.exists():
                    shutil.rmtree(model_dir)
                model_dir.mkdir(parents=True, exist_ok=True)
                report = train_scenario_model(
                    python,
                    dataset_root,
                    model_dir,
                    scenario,
                    candidate_info["available"],
                )
                model_dirs[(scenario, method)] = model_dir
                train_reports.append({
                    "scenario": scenario,
                    "sampling_method": method,
                    "model_dir": str(model_dir),
                    "report": report,
                })
        external_roots: dict[tuple[str, str], Path] = {}
        for scenario, source_key in (("llm_text", "llm_external"), ("metadata", "metadata_external")):
            for method in SAMPLING_METHODS:
                dataset_root = datasets_root / scenario / method / "external"
                records = collect_sampled_records(
                    dataset_root,
                    sources[source_key],
                    scenario=scenario,
                    method=method,
                    blocks_per_group=int(args.external_blocks_per_group),
                    external=True,
                    seed=SEED,
                )
                sampling_manifest.extend(records)
                external_roots[(scenario, method)] = dataset_root
        (run_root / "sampling_manifest.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in sampling_manifest) + "\n",
            encoding="utf-8",
        )
        write_csv(
            run_root / "training_summary.csv",
            [
                {
                    "scenario": row["scenario"],
                    "sampling_method": row["sampling_method"],
                    "model_dir": row["model_dir"],
                    "status": row["report"].get("status", ""),
                    "num_train": row["report"].get("num_train", ""),
                    "num_test": row["report"].get("num_test", ""),
                    "accuracy": row["report"].get("best_test_accuracy", ""),
                    "relative_cr": row["report"].get("best_test_relative_cr", ""),
                    "labels": json.dumps(row["report"].get("labels", []), ensure_ascii=False),
                }
                for row in train_reports
            ],
            [
                "scenario",
                "sampling_method",
                "model_dir",
                "status",
                "num_train",
                "num_test",
                "accuracy",
                "relative_cr",
                "labels",
            ],
        )
        run(["rsync", "-az", str(ROOT / "Model" / "scenarios") + "/", f"{REMOTE_ALIAS}:{REMOTE_PROJECT}/Model/scenarios/"])
        local_rows = run_local_metrics(run_root, external_roots, model_dirs, candidate_info["available"], python)
        write_csv(
            run_root / "local_compression_metrics.csv",
            local_rows,
            sorted({key for row in local_rows for key in row}),
        )
        if not args.skip_transport:
            execute_transport(run_root, local_rows)
        else:
            for row in local_rows:
                row.update({
                    "status": "transport_skipped",
                    "roundtrip_pass": None,
                    "transfer_time_sec": None,
                    "remote_decompress_time_sec": None,
                    "end_to_end_time_sec": None,
                })
        summaries = summarize(local_rows)
        write_csv(
            run_root / "gridflux_transfer_metrics.csv",
            local_rows,
            sorted({key for row in local_rows for key in row}),
        )
        write_csv(
            run_root / "method_comparison.csv",
            flatten_summaries(summaries),
            [
                "scenario",
                "external_sampling_method",
                "training_model",
                "rows",
                "ok_rows",
                "lossless_rate",
                "compression_ratio_median",
                "compress_time_median_sec",
                "transfer_time_median_sec",
                "end_to_end_time_median_sec",
                "logical_bandwidth_median_mib_s",
                "end_to_end_logical_bandwidth_median_mib_s",
                "selected_pipelines",
            ],
        )
        write_csv(
            run_root / "sampling_effect_summary.csv",
            flatten_summaries(summaries),
            [
                "scenario",
                "external_sampling_method",
                "training_model",
                "rows",
                "ok_rows",
                "lossless_rate",
                "compression_ratio_median",
                "compress_time_median_sec",
                "transfer_time_median_sec",
                "end_to_end_time_median_sec",
                "logical_bandwidth_median_mib_s",
                "end_to_end_logical_bandwidth_median_mib_s",
                "selected_pipelines",
            ],
        )
        status = "completed"
    except Exception as exc:
        blocker = f"{type(exc).__name__}: {exc}"
        print(f"EXPERIMENT BLOCKED: {blocker}", file=sys.stderr, flush=True)
    finally:
        if run_root.exists():
            for disposable in (archives, extracted_root, datasets_root, run_root / "staging", run_root / "gridflux_logs"):
                if disposable.exists():
                    shutil.rmtree(disposable)
        if local_venv.exists():
            shutil.rmtree(local_venv, ignore_errors=True)
        run_remote(
            "if [ -d /root/projects/science-compressor/.venv ]; then "
            "find /root/projects/science-compressor/.venv -depth -type f -delete; "
            "find /root/projects/science-compressor/.venv -depth -type d -empty -delete; fi",
            capture=True,
            check=False,
        )
        cleanup["status"] = "pass" if not any(
            path.exists() for path in (
                archives,
                extracted_root,
                datasets_root,
                run_root / "staging",
                run_root / "gridflux_logs",
                local_venv,
            )
        ) else "failed"
        write_report(
            run_root,
            status=status,
            blocker=blocker,
            object_info=object_info,
            candidate_info=candidate_info,
            train_reports=train_reports,
            summaries=summaries,
            sampling_manifest=sampling_manifest,
            cleanup=cleanup,
        )
        (run_root / "completion_audit.json").write_text(json.dumps({
            "status": status,
            "blocker": blocker,
            "cleanup": cleanup,
            "ssh_alias": REMOTE_ALIAS,
            "remote_host": REMOTE_HOST,
            "gridflux_port": GRIDFLUX_PORT,
            "created_at": now(),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        (run_root / "cleanup_audit.json").write_text(json.dumps(cleanup, indent=2), encoding="utf-8")
    print(f"run_root: {run_root}", flush=True)
    print(f"status: {status}", flush=True)
    print(f"cleanup: {cleanup['status']}", flush=True)
    return 0 if status == "completed" and cleanup["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

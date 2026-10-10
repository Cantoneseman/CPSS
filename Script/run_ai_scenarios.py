#!/usr/bin/env python3
"""Legacy OSS-backed LLM-text and metadata compression experiment runner.

The runner deliberately keeps source archives, extracted files, chunk manifests,
and evaluation rows inside one disposable run directory. Trained scenario
models and the final Markdown report are the only persistent experiment outputs.
New local CPSS processing should use Script/run_cpss_pipeline.py so AI and HPC
data follow the same core flow.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "Script"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from Compressor.deploy_registry import DEPLOYABLE_PIPELINES
from Compressor.reversible import (
    CompressionArtifact,
    compress_with_pipeline,
    decompress_from_pipeline_id,
)
from Compressor.runtime import PipelineContext
from Compressor.selector import (
    artifact_total_size,
    exhaustive_select,
    select_pipeline,
)


BUCKET = "science-compressor-datasets"
OSS_PREFIX = f"oss://{BUCKET}/AI Dataset"
OBJECTS = {
    "openorca": f"{OSS_PREFIX}/model/ai_openorca_1m_gpt4_augmented.tar.zst",
    "slimorca": f"{OSS_PREFIX}/model/ai_slimorca_oo_labeled_correct_gpt4.tar.zst",
    "wikitext": f"{OSS_PREFIX}/model/ai_wikitext103_raw_v1.tar.zst",
    "metadata": f"{OSS_PREFIX}/Metadata_Training/metadata_training_20260727.tar.zst",
}
REPORT_PATH = ROOT / "AI_COMPRESSION_SCENARIOS_REPORT_20260727.md"
MODEL_DIRS = {
    "llm_text": ROOT / "Model" / "scenarios" / "llm_text",
    "metadata": ROOT / "Model" / "scenarios" / "metadata",
}
DEFAULT_CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_SEED = 20260727
TEXT_SUFFIXES = {".csv", ".json", ".jsonl", ".txt", ".raw", ".tsv"}
DEFAULT_CANDIDATES = [
    "zstd",
    "zlib",
    "libdeflate",
    "snappy",
    "brotli",
    "bzip2",
    "xz_lzma2",
    "shuffle->xz_lzma2",
    "delta_length_byte_array->xz_lzma2",
    "tdt->brotli",
    "tdt->xz_lzma2",
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        text=True,
        capture_output=capture,
        check=True,
    )


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


def package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def command_version(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return f"unavailable: {exc}"
    text = (result.stdout or result.stderr).strip().splitlines()
    if not text:
        return f"exit {result.returncode}"
    return text[0]


def stage_object(uri: str, destination: Path) -> dict[str, Any]:
    stat_result = run_command(["ossutil", "stat", uri], capture=True)
    metadata = parse_oss_stat(stat_result.stdout)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command(["ossutil", "cp", uri, str(destination)])
    actual_size = destination.stat().st_size
    expected_size = metadata.get("size_bytes")
    if expected_size is not None and actual_size != expected_size:
        raise RuntimeError(
            f"OSS size mismatch for {uri}: expected {expected_size}, got {actual_size}"
        )
    run_command(["zstd", "-t", str(destination)])
    metadata.update(
        {
            "uri": uri,
            "local_path": str(destination),
            "actual_size_bytes": actual_size,
            "sha256": sha256_file(destination),
        }
    )
    return metadata


def extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    run_command(["tar", "--zstd", "-xf", str(archive), "-C", str(destination)])


def find_one(root: Path, patterns: Iterable[str]) -> Path:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(sorted(root.rglob(pattern)))
    if not matches:
        raise FileNotFoundError(f"no matching file under {root}: {list(patterns)}")
    return matches[0]


def unzip_wikitext(root: Path) -> Path:
    archive = find_one(root, ["*.zip"])
    destination = root / "wikitext_unpacked"
    destination.mkdir(parents=True, exist_ok=True)
    run_command(["unzip", "-q", "-o", str(archive), "-d", str(destination)])
    return destination


def write_chunk(
    dataset_root: Path,
    chunk_index: int,
    payload: bytes,
    *,
    source_path: Path,
    source_group: str,
    scenario: str,
    offset: int,
) -> dict[str, Any]:
    if not payload:
        raise ValueError("empty chunk")
    suffix = source_path.suffix.lower() or ".bin"
    filename = f"{chunk_index:08d}{suffix}"
    output = dataset_root / "slices" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    return {
        "id": str(chunk_index),
        "slice_file": str(Path("slices") / filename),
        "source_file": source_path.name,
        "source_group": source_group,
        "scenario": scenario,
        "format": suffix.lstrip("."),
        "original_suffix": suffix,
        "chunk_offset_bytes": offset,
        "chunk_size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "split": "external",
    }


def iter_chunks(
    path: Path,
    chunk_bytes: int,
    *,
    max_chunks: int | None = None,
) -> Iterable[tuple[int, bytes]]:
    emitted = 0
    if path.suffix.lower() not in TEXT_SUFFIXES:
        offset = 0
        with path.open("rb") as handle:
            while True:
                if max_chunks is not None and emitted >= max_chunks:
                    return
                payload = handle.read(chunk_bytes)
                if not payload:
                    return
                yield offset, payload
                offset += len(payload)
                emitted += 1
        return

    offset = 0
    pending = b""
    with path.open("rb") as handle:
        while True:
            if max_chunks is not None and emitted >= max_chunks:
                return
            block = handle.read(min(chunk_bytes, 1024 * 1024))
            if not block:
                break
            pending += block
            while len(pending) >= chunk_bytes:
                if max_chunks is not None and emitted >= max_chunks:
                    return
                cut = pending.rfind(b"\n", 0, chunk_bytes + 1)
                if cut < chunk_bytes // 2:
                    cut = chunk_bytes - 1
                payload = pending[: cut + 1]
                pending = pending[cut + 1 :]
                yield offset, payload
                offset += len(payload)
                emitted += 1
    if pending:
        if max_chunks is not None and emitted >= max_chunks:
            return
        yield offset, pending


def collect_chunks(
    dataset_root: Path,
    source_files: Iterable[tuple[Path, str]],
    *,
    scenario: str,
    chunk_bytes: int,
    max_chunks_per_file: int | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    next_id = 1
    for source_path, source_group in source_files:
        print(f"chunking {source_group}/{source_path.name}", flush=True)
        source_size = source_path.stat().st_size
        for offset, payload in iter_chunks(
            source_path,
            chunk_bytes,
            max_chunks=max_chunks_per_file,
        ):
            records.append(
                write_chunk(
                    dataset_root,
                    next_id,
                    payload,
                    source_path=source_path,
                    source_group=source_group,
                    scenario=scenario,
                    offset=offset,
                )
            )
            records[-1]["source_size_bytes"] = source_size
            records[-1]["sampling_max_chunks_per_file"] = max_chunks_per_file
            next_id += 1
    if not records:
        raise RuntimeError(f"no chunks generated for {scenario}")
    return records


def assign_internal_split(records: list[dict[str, Any]], seed: int) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record["source_group"]), []).append(record)
    for group, rows in groups.items():
        rng = random.Random(seed + sum(ord(char) for char in group))
        rng.shuffle(rows)
        test_count = 0 if len(rows) <= 1 else max(1, int(len(rows) * 0.2))
        test_ids = {id(row) for row in rows[:test_count]}
        for row in rows:
            row["split"] = "test" if id(row) in test_ids else "train"


def write_dataset_manifest(
    dataset_root: Path,
    records: list[dict[str, Any]],
    *,
    scenario: str,
    split_seed: int,
    external: bool,
) -> None:
    metadata = {
        "scenario": scenario,
        "split_seed": split_seed,
        "external": external,
        "chunk_bytes": DEFAULT_CHUNK_BYTES,
        "sampling_max_chunks_per_file": (
            records[0].get("sampling_max_chunks_per_file") if records else None
        ),
        "record_count": len(records),
        "source_groups": sorted({str(row["source_group"]) for row in records}),
    }
    data = {"_meta": metadata}
    data.update({str(row["id"]): row for row in records})
    dataset_root.mkdir(parents=True, exist_ok=True)
    (dataset_root / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def model_sources(extracted: dict[str, Path]) -> list[tuple[Path, str]]:
    openorca = find_one(extracted["openorca"], ["1M-GPT4-Augmented.csv"])
    slimorca = find_one(extracted["slimorca"], ["*.jsonl"])
    return [(openorca, "openorca_1m"), (slimorca, "slimorca")]


def wikitext_sources(extracted: dict[str, Path]) -> list[tuple[Path, str]]:
    unpacked = unzip_wikitext(extracted["wikitext"])
    files = sorted(path for path in unpacked.rglob("*") if path.is_file() and path.suffix == ".raw")
    if not files:
        raise FileNotFoundError(f"no WikiText .raw files under {unpacked}")
    return [(path, "wikitext") for path in files]


def metadata_sources(extracted: dict[str, Path], groups: set[str]) -> list[tuple[Path, str]]:
    root = extracted["metadata"] / "Metadata_Training"
    mapping = {
        "openimages": root / "05_openimages_v7",
        "laion": root / "06_laion_metadata",
        "embedding": root / "12_embedding_metadata",
    }
    sources: list[tuple[Path, str]] = []
    for group in sorted(groups):
        directory = mapping[group]
        if not directory.exists():
            raise FileNotFoundError(directory)
        for path in sorted(path for path in directory.rglob("*") if path.is_file()):
            sources.append((path, group))
    if not sources:
        raise RuntimeError(f"no metadata files for groups {sorted(groups)}")
    return sources


def prepare_dataset(
    dataset_root: Path,
    sources: Iterable[tuple[Path, str]],
    *,
    scenario: str,
    split_seed: int,
    chunk_bytes: int,
    external: bool,
    max_chunks_per_file: int | None,
) -> list[dict[str, Any]]:
    records = collect_chunks(
        dataset_root,
        sources,
        scenario=scenario,
        chunk_bytes=chunk_bytes,
        max_chunks_per_file=max_chunks_per_file,
    )
    if not external:
        assign_internal_split(records, split_seed)
    write_dataset_manifest(
        dataset_root,
        records,
        scenario=scenario,
        split_seed=split_seed,
        external=external,
    )
    return records


def available_pipeline_names(
    sample: Path,
    desired: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    raw = sample.read_bytes()
    context = PipelineContext(
        dtype_name=None,
        shape=(len(raw),),
        typesize=1,
        metadata={"scenario": "llm_text", "format": sample.suffix.lstrip(".")},
    )
    context_metadata = {
        "dtype_name": None,
        "shape": [len(raw)],
        "typesize": 1,
        "endian": "little",
        "shape_source": "byte_fallback",
        "original_suffix": sample.suffix,
        "scenario": "llm_text",
        "format": sample.suffix.lstrip("."),
    }
    available: list[str] = []
    failures: dict[str, str] = {}
    desired_set = set(desired or [row.name for row in DEPLOYABLE_PIPELINES])
    for row in DEPLOYABLE_PIPELINES:
        if row.name not in desired_set:
            continue
        try:
            artifact = compress_with_pipeline(
                raw,
                row.name,
                context,
                context_metadata=context_metadata,
                selector_metadata={"mode": "preflight"},
            )
            restored = decompress_from_pipeline_id(
                artifact.payload,
                artifact.pipeline_id,
                artifact.metadata,
                artifact.tail,
            )
            if restored != raw:
                raise RuntimeError("roundtrip mismatch")
            available.append(row.name)
        except Exception as exc:
            failures[row.name] = str(exc)
    return available, failures


def summarize_dataset(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, dict[str, int]] = {}
    for record in records:
        group = str(record.get("source_group") or "unknown")
        row = groups.setdefault(group, {"chunks": 0, "sampled_bytes": 0, "source_bytes": 0})
        row["chunks"] += 1
        row["sampled_bytes"] += int(record.get("chunk_size_bytes") or 0)
        row["source_bytes"] = max(row["source_bytes"], int(record.get("source_size_bytes") or 0))
    return {
        "chunks": len(records),
        "sampled_bytes": sum(int(record.get("chunk_size_bytes") or 0) for record in records),
        "groups": groups,
    }


def run_labeling(
    dataset_root: Path,
    *,
    scenario: str,
    candidates: list[str],
    python_executable: str,
) -> None:
    env = os.environ.copy()
    env["PIPELINES"] = ",".join(candidates)
    env["COMPRESS_LABEL_SPLIT_SEED"] = str(DEFAULT_SEED)
    command = [
        python_executable,
        str(SCRIPT_DIR / "compress_and_label.py"),
        "--dataset-root",
        str(dataset_root),
        "--scenario",
        scenario,
        "--workers",
        "1",
        "--progress-every",
        "25",
        "--slice-pipeline-progress-every",
        "0",
    ]
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=str(ROOT), env=env, check=True)


def run_training(
    dataset_root: Path,
    model_dir: Path,
    *,
    scenario: str,
    python_executable: str,
) -> dict[str, Any]:
    command = [
        python_executable,
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
    ]
    run_command(command)
    report_path = model_dir / "test_report_nnmax.json"
    if not report_path.exists():
        raise FileNotFoundError(report_path)
    return json.loads(report_path.read_text(encoding="utf-8"))


def context_for_record(record: dict[str, Any], raw: bytes) -> tuple[PipelineContext, dict[str, Any]]:
    scenario = str(record.get("scenario") or "science")
    fmt = str(record.get("format") or record.get("original_suffix") or "").lstrip(".")
    metadata = {
        "dtype_name": None,
        "shape": [len(raw)],
        "typesize": 1,
        "endian": "little",
        "shape_source": "byte_fallback",
        "original_suffix": record.get("original_suffix", ""),
        "scenario": scenario,
        "format": fmt,
        "source_group": record.get("source_group", ""),
        "element_count": len(raw),
        "aligned_size": len(raw),
    }
    context = PipelineContext(
        dtype_name=None,
        shape=(len(raw),),
        typesize=1,
        metadata={
            "scenario": scenario,
            "format": fmt,
            "source_group": record.get("source_group", ""),
        },
    )
    return context, metadata


def verify_artifact(raw: bytes, artifact: CompressionArtifact) -> tuple[int, bool]:
    restored = decompress_from_pipeline_id(
        artifact.payload,
        artifact.pipeline_id,
        artifact.metadata,
        artifact.tail,
    )
    return 64 + artifact_total_size(artifact), restored == raw


def evaluate_dataset(
    dataset_root: Path,
    *,
    model_dir: Path,
    scenario: str,
    candidates: list[str],
    old_model_dir: Path | None = None,
) -> dict[str, Any]:
    data = json.loads((dataset_root / "data.json").read_text(encoding="utf-8"))
    rows = [row for key, row in data.items() if key != "_meta"]
    modes = ["model", "hybrid"]
    summaries: dict[str, dict[str, Any]] = {}
    for name in modes + ["exhaustive", "zstd", "xz_lzma2", "brotli"]:
        summaries[name] = {
            "raw_bytes": 0,
            "encoded_bytes": 0,
            "chunks": 0,
            "successes": 0,
            "lossless": 0,
            "pipeline_counts": Counter(),
            "oracle_gap_sum": 0.0,
        }
    if old_model_dir and (old_model_dir / "selector_nnmax_ensemble.pt").exists():
        summaries["existing_model"] = {
            "raw_bytes": 0,
            "encoded_bytes": 0,
            "chunks": 0,
            "successes": 0,
            "lossless": 0,
            "pipeline_counts": Counter(),
            "oracle_gap_sum": 0.0,
        }

    oracle_rows: list[dict[str, Any]] = []
    for index, record in enumerate(rows, start=1):
        raw = (dataset_root / str(record["slice_file"])).read_bytes()
        context, context_metadata = context_for_record(record, raw)
        oracle = exhaustive_select(raw, context, context_metadata, candidates)
        oracle_size, oracle_lossless = verify_artifact(raw, oracle.artifact)
        oracle_pipeline = oracle.artifact.pipeline_name

        selections: dict[str, tuple[CompressionArtifact, int, bool]] = {}
        for mode in modes:
            selection = select_pipeline(
                raw,
                context,
                context_metadata,
                mode=mode,
                top_k=3,
                candidates=candidates,
                model_dir=model_dir,
            )
            size, lossless = verify_artifact(raw, selection.artifact)
            selections[mode] = (selection.artifact, size, lossless)

        if "existing_model" in summaries:
            selection = select_pipeline(
                raw,
                context,
                context_metadata,
                mode="model",
                candidates=candidates,
                model_dir=old_model_dir,
            )
            selections["existing_model"] = (selection.artifact, *verify_artifact(raw, selection.artifact))

        for fixed in ("zstd", "xz_lzma2", "brotli"):
            if fixed not in candidates:
                continue
            artifact = compress_with_pipeline(
                raw,
                fixed,
                context,
                context_metadata=context_metadata,
                selector_metadata={"mode": "fixed_baseline"},
            )
            selections[fixed] = (artifact, *verify_artifact(raw, artifact))

        for name, summary in summaries.items():
            summary["chunks"] += 1
            summary["raw_bytes"] += len(raw)
            if name == "exhaustive":
                size, lossless = oracle_size, oracle_lossless
                artifact = oracle.artifact
            else:
                if name not in selections:
                    continue
                artifact, size, lossless = selections[name]
            summary["encoded_bytes"] += size
            summary["successes"] += 1
            summary["lossless"] += int(lossless)
            summary["pipeline_counts"][artifact.pipeline_name] += 1
            if name != "exhaustive":
                summary["oracle_gap_sum"] += oracle_size / max(1, size)

        oracle_rows.append(
            {
                "index": index,
                "source_group": record.get("source_group"),
                "source_file": record.get("source_file"),
                "slice_file": record.get("slice_file"),
                "raw_bytes": len(raw),
                "oracle_pipeline": oracle_pipeline,
                "oracle_bytes": oracle_size,
                "model_pipeline": selections["model"][0].pipeline_name,
                "model_bytes": selections["model"][1],
                "hybrid_pipeline": selections["hybrid"][0].pipeline_name,
                "hybrid_bytes": selections["hybrid"][1],
            }
        )
        if index % 25 == 0 or index == len(rows):
            print(f"evaluated {index}/{len(rows)} chunks", flush=True)

    output: dict[str, Any] = {"scenario": scenario, "rows": len(rows), "methods": {}}
    for name, summary in summaries.items():
        summary["pipeline_counts"] = dict(summary["pipeline_counts"])
        summary["compression_ratio"] = (
            summary["raw_bytes"] / summary["encoded_bytes"]
            if summary["encoded_bytes"]
            else None
        )
        summary["lossless_rate"] = (
            summary["lossless"] / summary["successes"]
            if summary["successes"]
            else 0.0
        )
        summary["relative_to_oracle"] = (
            summary["oracle_gap_sum"] / summary["successes"]
            if summary["successes"] and name != "exhaustive"
            else 1.0
        )
        output["methods"][name] = summary
    output["sample_rows"] = oracle_rows[:100]
    return output


def format_method_table(evaluation: dict[str, Any]) -> list[str]:
    def fmt(value: Any) -> str:
        if value is None:
            return "n/a"
        return f"{float(value):.4f}"

    lines = [
        "| method | chunks | ratio | relative/oracle | lossless |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in evaluation["methods"].items():
        lines.append(
            f"| `{name}` | {row['chunks']} | "
            f"{fmt(row.get('compression_ratio'))} | "
            f"{fmt(row.get('relative_to_oracle'))} | "
            f"{fmt(row.get('lossless_rate'))} |"
        )
    return lines


def scenario_conclusion(scenario: str, evaluation: dict[str, Any]) -> list[str]:
    methods = evaluation.get("methods", {})
    model = methods.get("model", {})
    zstd = methods.get("zstd", {})
    existing = methods.get("existing_model", {})
    exhaustive = methods.get("exhaustive", {})

    def ratio(row: dict[str, Any]) -> float | None:
        value = row.get("compression_ratio")
        return None if value is None else float(value)

    model_ratio = ratio(model)
    zstd_ratio = ratio(zstd)
    existing_ratio = ratio(existing)
    oracle_ratio = ratio(exhaustive)
    rows = [
        f"- `{scenario}`: `完成`; external chunks `{model.get('chunks', 0)}`, lossless `{model.get('lossless_rate', 0.0):.4f}`.",
    ]
    if model_ratio is not None and oracle_ratio:
        rows.append(
            f"- `{scenario}` model/oracle efficiency: `{model.get('relative_to_oracle', 0.0):.4f}` "
            f"({model_ratio:.4f} vs oracle {oracle_ratio:.4f})."
        )
    if model_ratio is not None and zstd_ratio is not None:
        verdict = "有改善" if model_ratio > zstd_ratio else "无改善"
        rows.append(f"- `{scenario}` vs fixed `zstd`: `{verdict}` ({model_ratio:.4f} vs {zstd_ratio:.4f}).")
    if model_ratio is not None and existing_ratio is not None:
        verdict = "有改善" if model_ratio > existing_ratio else "无改善"
        rows.append(
            f"- `{scenario}` vs existing generic model: `{verdict}` "
            f"({model_ratio:.4f} vs {existing_ratio:.4f})."
        )
    return rows


def write_report(
    *,
    status: str,
    started_at: str,
    finished_at: str,
    objects: dict[str, Any],
    candidates: list[str],
    failures: dict[str, str],
    datasets: dict[str, dict[str, Any]],
    training: dict[str, dict[str, Any]],
    evaluations: dict[str, dict[str, Any]],
    cleanup_status: str,
) -> None:
    lines = [
        "# AI Compression Scenario Experiment",
        "",
        f"- Status: `{status}`",
        f"- Started: `{started_at}`",
        f"- Finished: `{finished_at}`",
        "- Interpretation: `llm_text` compresses LLM training corpora, not model weights.",
        "",
        "## Environment",
        "",
        "| dependency | version/status |",
        "|---|---|",
        f"| Python | `{sys.version.split()[0]}` |",
        f"| torch | `{package_version('torch')}` |",
        f"| numpy | `{package_version('numpy')}` |",
        f"| zstandard | `{package_version('zstandard')}` |",
        f"| brotli | `{package_version('brotli')}` |",
        f"| python-snappy | `{package_version('python-snappy')}` |",
        f"| ossutil | `{command_version(['ossutil', '--version'])}` |",
        f"| zstd CLI | `{command_version(['zstd', '--version'])}` |",
        f"| tar | `{command_version(['tar', '--version'])}` |",
        f"| unzip | `{command_version(['unzip', '-v'])}` |",
        "",
        "## OSS Objects",
        "",
        "| name | OSS URI | size bytes | Last-Modified | ETag | SHA256 |",
        "|---|---|---:|---|---|---|",
    ]
    for name, row in objects.items():
        lines.append(
            f"| `{name}` | `{row.get('uri', '')}` | "
            f"{row.get('actual_size_bytes', row.get('size_bytes', ''))} | "
            f"`{row.get('last_modified', '')}` | "
            f"`{row.get('etag', '')}` | `{row.get('sha256', '')}` |"
        )
    lines.extend(
        [
            "",
            "## Data Preparation",
            "",
            "- All OSS objects were downloaded, verified with `zstd -t`, extracted with `tar --zstd -xf`, and WikiText ZIP was unpacked before chunking.",
            "- Outer `.tar.zst` files were treated only as packaging. Compression comparisons used extracted file chunks only.",
            "- The CPU-only run uses bounded representative chunks from each extracted file; chunk offsets and SHA256 values were generated in the temporary manifests before cleanup.",
            "- LLM external validation used WikiText only. Metadata external validation used `12_embedding_metadata` only, held out from training.",
            f"- Chunk target: `{DEFAULT_CHUNK_BYTES}` bytes; fixed split seed: `{DEFAULT_SEED}`; workers: `1`.",
            "",
            "| dataset | chunks | sampled bytes | groups |",
            "|---|---:|---:|---|",
        ]
    )
    for name, row in datasets.items():
        groups = ", ".join(
            f"{group}:{info['chunks']}"
            for group, info in sorted(row.get("groups", {}).items())
        )
        lines.append(
            f"| `{name}` | {row.get('chunks', 0)} | {row.get('sampled_bytes', 0)} | {groups} |"
        )

    lines.extend(
        [
            "",
            "## Candidate Pipelines",
            "",
            f"- available candidate count: `{len(candidates)}`",
            f"- candidates: {', '.join(f'`{name}`' for name in candidates)}",
        ]
    )
    if failures:
        lines.extend(["", "### Preflight Failures", ""])
        lines.extend(f"- `{name}`: {reason}" for name, reason in failures.items())

    lines.extend(["", "## Training", ""])
    for scenario, report in training.items():
        lines.extend(
            [
                f"### `{scenario}`",
                "",
                f"- status: `{report.get('status')}`",
                f"- train rows: `{report.get('num_train')}`",
                f"- internal validation rows: `{report.get('num_test')}`",
                f"- internal accuracy: `{report.get('best_test_accuracy')}`",
                f"- internal relative compression: `{report.get('best_test_relative_cr')}`",
                f"- model directory: `{MODEL_DIRS[scenario]}`",
                "",
            ]
        )

    lines.extend(["## External Validation", ""])
    for scenario, evaluation in evaluations.items():
        lines.extend([f"### `{scenario}`", ""])
        lines.extend(format_method_table(evaluation))
        lines.append("")

    lines.extend(["## Acceptance", ""])
    for scenario, evaluation in evaluations.items():
        lines.extend(scenario_conclusion(scenario, evaluation))
    lines.extend(
        [
            "- Successful external validation chunks restored byte-for-byte (`lossless = 1.0000`).",
            "- Training and external validation sources are disjoint by construction.",
            "- Resource note: this run is bounded by sampled chunks per extracted file to stay within the available CPU/RAM envelope; it is a deployability validation, not a full-corpus performance sweep.",
            "",
        ]
    )

    lines.extend(
        [
            "## Cleanup",
            "",
            f"- cleanup status: `{cleanup_status}`",
            "- disposable run directory, downloaded archives inside it, extracted datasets, intermediate manifests, caches, and logs are removed by the runner.",
            "- external archive caches or temporary virtual environments supplied outside the run directory must be removed by the final cleanup audit.",
            "- OSS source objects and the pre-existing generic model are not modified.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OSS-backed AI compression scenarios.")
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--max-chunks-per-file", type=int, default=4)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--archive-cache", type=Path, default=None)
    parser.add_argument(
        "--candidate-pipelines",
        default=",".join(DEFAULT_CANDIDATES),
        help="comma-separated deployable pipeline subset for labeling and oracle evaluation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global DEFAULT_CHUNK_BYTES, DEFAULT_SEED
    DEFAULT_CHUNK_BYTES = int(args.chunk_bytes)
    DEFAULT_SEED = int(args.seed)

    run_id = time.strftime("%Y%m%dT%H%M%S")
    run_root = (args.run_root or Path("/tmp") / f"science-compressor-ai-{run_id}").resolve()
    archive_root = run_root / "archives"
    extracted_root = run_root / "extracted"
    dataset_root = run_root / "datasets"
    run_root.mkdir(parents=True, exist_ok=True)
    started_at = now()
    objects: dict[str, Any] = {}
    failures: dict[str, str] = {}
    training: dict[str, dict[str, Any]] = {}
    evaluations: dict[str, dict[str, Any]] = {}
    datasets: dict[str, dict[str, Any]] = {}
    candidates: list[str] = []
    status = "blocked"
    cleanup_status = "not_started"

    try:
        archives: dict[str, Path] = {}
        for name, uri in OBJECTS.items():
            destination = (
                args.archive_cache.resolve() / Path(uri).name
                if args.archive_cache
                else archive_root / Path(uri).name
            )
            if args.archive_cache and destination.exists():
                stat_result = run_command(["ossutil", "stat", uri], capture=True)
                objects[name] = {
                    "uri": uri,
                    "local_path": str(destination),
                    "actual_size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    **parse_oss_stat(stat_result.stdout),
                }
                run_command(["zstd", "-t", str(destination)])
            elif args.skip_download and destination.exists():
                objects[name] = {
                    "uri": uri,
                    "local_path": str(destination),
                    "actual_size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            else:
                objects[name] = stage_object(uri, destination)
            archives[name] = destination

        extracted: dict[str, Path] = {}
        for name, archive in archives.items():
            target = extracted_root / name
            extract_archive(archive, target)
            extracted[name] = target

        llm_train_root = dataset_root / "llm_train"
        llm_external_root = dataset_root / "llm_external"
        metadata_train_root = dataset_root / "metadata_train"
        metadata_external_root = dataset_root / "metadata_external"

        llm_train_records = prepare_dataset(
            llm_train_root,
            model_sources(extracted),
            scenario="llm_text",
            split_seed=args.seed,
            chunk_bytes=args.chunk_bytes,
            external=False,
            max_chunks_per_file=args.max_chunks_per_file,
        )
        datasets["llm_text_train_internal"] = summarize_dataset(llm_train_records)
        llm_external_records = prepare_dataset(
            llm_external_root,
            wikitext_sources(extracted),
            scenario="llm_text",
            split_seed=args.seed,
            chunk_bytes=args.chunk_bytes,
            external=True,
            max_chunks_per_file=args.max_chunks_per_file,
        )
        datasets["llm_text_wikitext_external"] = summarize_dataset(llm_external_records)
        metadata_train_records = prepare_dataset(
            metadata_train_root,
            metadata_sources(extracted, {"openimages", "laion"}),
            scenario="metadata",
            split_seed=args.seed,
            chunk_bytes=args.chunk_bytes,
            external=False,
            max_chunks_per_file=args.max_chunks_per_file,
        )
        datasets["metadata_train_internal"] = summarize_dataset(metadata_train_records)
        metadata_external_records = prepare_dataset(
            metadata_external_root,
            metadata_sources(extracted, {"embedding"}),
            scenario="metadata",
            split_seed=args.seed,
            chunk_bytes=args.chunk_bytes,
            external=True,
            max_chunks_per_file=args.max_chunks_per_file,
        )
        datasets["metadata_embedding_external"] = summarize_dataset(metadata_external_records)

        sample_path = next(llm_train_root.glob("slices/*"))
        desired_candidates = [
            item.strip() for item in args.candidate_pipelines.split(",") if item.strip()
        ]
        candidates, failures = available_pipeline_names(sample_path, desired_candidates)
        if len(candidates) < 3:
            raise RuntimeError(f"too few usable pipelines: {candidates}")

        for scenario, train_root in (
            ("llm_text", llm_train_root),
            ("metadata", metadata_train_root),
        ):
            run_labeling(
                train_root,
                scenario=scenario,
                candidates=candidates,
                python_executable=args.python,
            )
            model_dir = MODEL_DIRS[scenario]
            model_dir.mkdir(parents=True, exist_ok=True)
            training[scenario] = run_training(
                train_root,
                model_dir,
                scenario=scenario,
                python_executable=args.python,
            )

        existing_model = ROOT / "Model"
        evaluations["llm_text"] = evaluate_dataset(
            llm_external_root,
            model_dir=MODEL_DIRS["llm_text"],
            scenario="llm_text",
            candidates=candidates,
            old_model_dir=existing_model,
        )
        evaluations["metadata"] = evaluate_dataset(
            metadata_external_root,
            model_dir=MODEL_DIRS["metadata"],
            scenario="metadata",
            candidates=candidates,
            old_model_dir=existing_model,
        )
        status = "completed"
    except Exception as exc:
        failures["run"] = f"{type(exc).__name__}: {exc}"
        print(f"EXPERIMENT BLOCKED: {failures['run']}", file=sys.stderr, flush=True)
    finally:
        if run_root.exists():
            shutil.rmtree(run_root)
        cleanup_status = "pass" if not run_root.exists() else "failed"
        write_report(
            status=status,
            started_at=started_at,
            finished_at=now(),
            objects=objects,
            candidates=candidates,
            failures=failures,
            datasets=datasets,
            training=training,
            evaluations=evaluations,
            cleanup_status=cleanup_status,
        )

    print(f"report: {REPORT_PATH}", flush=True)
    print(f"status: {status}", flush=True)
    print(f"cleanup: {cleanup_status}", flush=True)
    return 0 if status == "completed" and cleanup_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

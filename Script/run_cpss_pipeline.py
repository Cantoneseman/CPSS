#!/usr/bin/env python3
"""Unified CPSS data-processing entrypoint.

This is the recommended runner for local CPSS processing. Historical experiment
matrix scripts remain available for legacy GridFlux-specific runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Compressor.pipeline_runner import (  # noqa: E402
    DEFAULT_BLOCK_SIZE_BYTES,
    DEFAULT_RANDOM_SEED,
    DEFAULT_SAMPLING_MODE,
    SCENARIO_MODEL_DIRS,
    STRATEGIES,
    PipelineRunConfig,
    default_result_dir,
    parse_pipeline_list,
    run_pipeline,
)


def parse_shape(text: str | None) -> tuple[int, ...] | None:
    if not text:
        return None
    return tuple(int(item.strip()) for item in text.split(",") if item.strip())


def parse_dedup(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"on", "true", "yes", "1"}:
        return True
    if normalized in {"off", "false", "no", "0"}:
        return False
    raise argparse.ArgumentTypeError("dedup must be on or off")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified CPSS pipeline runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-dir", type=Path, help="local input directory")
    source.add_argument("--oss-uri", type=str, help="OSS .tar.zst object to download, verify, and extract")
    parser.add_argument("--scenario", choices=sorted(SCENARIO_MODEL_DIRS), default="science")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default=DEFAULT_SAMPLING_MODE)
    parser.add_argument("--dedup", type=parse_dedup, default=True, metavar="on|off")
    parser.add_argument("--block-mib", type=int, default=DEFAULT_BLOCK_SIZE_BYTES // (1024 * 1024))
    parser.add_argument("--result-dir", type=Path, default=None)
    parser.add_argument("--fixed-pipeline", type=str, default="zstd")
    parser.add_argument("--pipelines", type=str, default=None, help="comma-separated deployable pipeline whitelist")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--sample-count", type=int, default=5)
    parser.add_argument("--random-seed", type=int, default=DEFAULT_RANDOM_SEED)
    parser.add_argument("--dtype", type=str, default=None, help="optional dtype override, e.g. float32")
    parser.add_argument("--shape", type=str, default=None, help="optional full array shape, e.g. 1800,3600")
    parser.add_argument("--endian", choices=["little", "big"], default="little")
    parser.add_argument("--datasets-metadata", type=Path, default=None, help="path to full-v1 metadata/datasets.json")
    parser.add_argument("--dataset-slug", type=str, default=None, help="dataset slug to load from datasets metadata")
    parser.add_argument("--bandwidth-Bps", dest="bandwidth_Bps", type=float, default=None, help="explicit system-model bandwidth in bytes/s")
    parser.add_argument("--min-expected-speedup", type=float, default=1.0)
    parser.add_argument("--guard-margin", type=float, default=0.0)
    parser.add_argument("--no-full-data-global-oracle", action="store_true")
    parser.add_argument("--no-verify", action="store_true", help="skip local CPSS bundle rebuild verification")
    parser.add_argument("--keep-rebuild", action="store_true", help="keep local rebuild directory after verification")
    parser.add_argument("--keep-prepared", action="store_true", help="keep downloaded/extracted OSS prepared data")
    parser.add_argument(
        "--transport",
        choices=["local", "gridflux"],
        default="local",
        help="GridFlux is still handled by legacy matrix wrappers in this refactor stage",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.transport == "gridflux":
        parser.error("GridFlux transfer remains in legacy runners for now; use local here or run the legacy matrix wrapper.")
    if args.block_mib <= 0:
        parser.error("--block-mib must be positive")
    result_dir = (args.result_dir or default_result_dir()).resolve()
    config = PipelineRunConfig(
        input_dir=args.input_dir,
        oss_uri=args.oss_uri,
        scenario=args.scenario,
        strategy=args.strategy,
        dedup=args.dedup,
        block_size_bytes=args.block_mib * 1024 * 1024,
        result_dir=result_dir,
        fixed_pipeline=args.fixed_pipeline,
        pipelines=parse_pipeline_list(args.pipelines),
        model_dir=args.model_dir,
        top_k=args.top_k,
        sample_count=args.sample_count,
        random_seed=args.random_seed,
        dtype_name=args.dtype,
        shape=parse_shape(args.shape),
        endian=args.endian,
        datasets_metadata_path=args.datasets_metadata,
        dataset_slug=args.dataset_slug,
        bandwidth_Bps=args.bandwidth_Bps,
        min_expected_speedup=args.min_expected_speedup,
        guard_margin=args.guard_margin,
        full_data_global_oracle=not args.no_full_data_global_oracle,
        verify_rebuild=not args.no_verify,
        keep_rebuild=args.keep_rebuild,
        keep_prepared=args.keep_prepared,
    )
    result = run_pipeline(config)
    print(json.dumps({
        "status": result.status,
        "run_id": result.run_id,
        "result_dir": str(result.result_dir),
        "source_manifest": str(result.source_manifest_path),
        "bundle_manifest": str(result.bundle_manifest_path) if result.bundle_manifest_path else None,
        "method_metrics": str(result.method_metrics_path),
        "summary": str(result.summary_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

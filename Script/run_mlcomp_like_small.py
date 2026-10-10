#!/usr/bin/env python3
"""Small, reproducible MLcomp-like mechanism comparison for CPSS.

This experiment is deliberately not a reimplementation of the DCC 2024
CRUSHER study.  It uses the CPSS deployment candidate set on a capped prefix
of eight local workloads, learns a 1NN selector from probe inverse-CR features,
and reports cross-workload CR and a bandwidth-aware estimated cost.
"""

from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Compressor.cpss_sampling_matrix import compress_fixed_pipeline_blob
from Compressor.dedup_manifest import decompress_cpss_blob


# These are the four broadly available fixed pipelines in the deployed
# registry.  The formal CPSS probe list also includes delta->zstd, but that
# experimental transform is not deployable for every byte-oriented context;
# keeping it out here makes the comparison a valid all-success baseline.
CANDIDATES = ["zstd", "lz4", "zlib", "xz_lzma2"]
DATASETS = [
    ("cesm_atm", "train"),
    ("hurricane_isabel", "train"),
    ("hacc", "train"),
    ("miranda", "train"),
    ("nyx", "validation"),
    ("scale_letkf", "validation"),
    ("exafel", "test"),
    ("rcsb_mmcif", "test"),
]


def first_data_file(raw_root: Path, cap: int) -> Path:
    files = sorted(p for p in raw_root.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(raw_root)
    suitable = [p for p in files if p.stat().st_size >= cap]
    return max(suitable or files, key=lambda p: p.stat().st_size)


def compress_measure(raw: bytes, pipeline: str) -> dict[str, float | int | str]:
    started = time.perf_counter()
    blob, meta = compress_fixed_pipeline_blob(
        raw,
        pipeline,
        selector_mode="mlcomp_like_probe",
        selector_metadata={"mode": "mlcomp_like_probe", "pipeline": pipeline},
    )
    comp_s = time.perf_counter() - started
    started = time.perf_counter()
    restored = decompress_cpss_blob(blob)
    decomp_s = time.perf_counter() - started
    if restored != raw:
        raise RuntimeError(f"roundtrip failed for {pipeline}")
    return {
        "pipeline": str(meta["pipeline_name"]),
        "bytes": int(len(blob)),
        "comp_s": float(comp_s),
        "decomp_s": float(decomp_s),
    }


def evaluate_1nn(train_rows: list[dict], query: dict, features: list[str]) -> str:
    best = None
    best_distance = math.inf
    for row in train_rows:
        distance = math.sqrt(sum(
            (float(query["features"][name]) - float(row["features"][name])) ** 2
            for name in features
        ))
        if distance < best_distance:
            best_distance = distance
            best = row["oracle_pipeline"]
    if best is None:
        raise RuntimeError("empty 1NN training set")
    return str(best)


def geometric_rel_cr(rows: list[dict], features: list[str], train_rows: list[dict]) -> float:
    values = []
    for row in rows:
        pred = evaluate_1nn(train_rows, row, features)
        predicted = next(item for item in row["full"] if item["pipeline"] == pred)
        values.append(float(row["oracle_bytes"]) / float(predicted["bytes"]))
    return math.prod(values) ** (1.0 / len(values)) if values else float("nan")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--object-mib", type=int, default=32)
    parser.add_argument("--probe-mib", type=int, default=4)
    parser.add_argument("--bandwidth-mbps", type=float, default=100.0)
    args = parser.parse_args()

    object_cap = int(args.object_mib) * 1024 * 1024
    probe_cap = int(args.probe_mib) * 1024 * 1024
    bandwidth_Bps = float(args.bandwidth_mbps) * 1_000_000 / 8.0
    if probe_cap > object_cap:
        raise SystemExit("probe cap must not exceed object cap")

    rows: list[dict] = []
    for dataset, split in DATASETS:
        path = first_data_file(args.dataset_root / dataset / "raw", object_cap)
        raw = path.read_bytes()[:object_cap]
        probe = raw[:probe_cap]
        full = [compress_measure(raw, candidate) for candidate in CANDIDATES]
        probe_stats = [compress_measure(probe, candidate) for candidate in CANDIDATES]
        oracle = min(full, key=lambda item: int(item["bytes"]))
        features = {
            item["pipeline"]: float(item["bytes"]) / float(len(probe))
            for item in probe_stats
        }
        probe_s = sum(float(item["comp_s"]) + float(item["decomp_s"]) for item in probe_stats)
        rows.append({
            "dataset": dataset,
            "split": split,
            "source_file": str(path),
            "raw_bytes": len(raw),
            "probe_bytes": len(probe),
            "features": features,
            "full": full,
            "oracle_pipeline": oracle["pipeline"],
            "oracle_bytes": int(oracle["bytes"]),
            "probe_s": probe_s,
        })

    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    test = [row for row in rows if row["split"] == "test"]

    # Forward SFS over the four probe pipelines, scoring validation geometric
    # relative CR.  The selected vector is then applied to a train-only 1NN.
    selected: list[str] = []
    remaining = list(CANDIDATES)
    sfs_trace = []
    while remaining and len(selected) < 2:
        scored = []
        for candidate in remaining:
            trial = selected + [candidate]
            scored.append((geometric_rel_cr(validation, trial, train), candidate))
        score, candidate = max(scored, key=lambda item: (item[0], item[1]))
        selected.append(candidate)
        remaining.remove(candidate)
        sfs_trace.append({"features": list(selected), "validation_geomean_rel_cr": score})

    # Also report the all-feature variant so the result is not dependent on SFS.
    def summarize(split_rows: list[dict], feature_names: list[str]) -> dict:
        predicted = []
        for row in split_rows:
            pred = evaluate_1nn(train, row, feature_names)
            pred_stats = next(item for item in row["full"] if item["pipeline"] == pred)
            oracle = next(item for item in row["full"] if item["pipeline"] == row["oracle_pipeline"])
            # Include probe work in the one-shot estimate.  This is an estimate,
            # not a claim of a second physical GridFlux transfer.
            predicted_cost = row["probe_s"] + float(pred_stats["comp_s"]) + float(pred_stats["bytes"]) / bandwidth_Bps + float(pred_stats["decomp_s"])
            zstd = next(item for item in row["full"] if item["pipeline"] == "zstd")
            raw_cost = float(row["raw_bytes"]) / bandwidth_Bps
            zstd_cost = float(zstd["comp_s"]) + float(zstd["bytes"]) / bandwidth_Bps + float(zstd["decomp_s"])
            predicted.append({
                "dataset": row["dataset"],
                "predicted_pipeline": pred,
                "oracle_pipeline": row["oracle_pipeline"],
                "relative_cr": float(row["oracle_bytes"]) / float(pred_stats["bytes"]),
                "estimated_mlcomp_cost_s": predicted_cost,
                "estimated_raw_cost_s": raw_cost,
                "estimated_zstd_cost_s": zstd_cost,
            })
        if not predicted:
            return {"rows": 0}
        return {
            "rows": len(predicted),
            "geomean_relative_cr": math.prod(item["relative_cr"] for item in predicted) ** (1.0 / len(predicted)),
            "mlcomp_cost_sum_s": sum(item["estimated_mlcomp_cost_s"] for item in predicted),
            "raw_cost_sum_s": sum(item["estimated_raw_cost_s"] for item in predicted),
            "zstd_cost_sum_s": sum(item["estimated_zstd_cost_s"] for item in predicted),
            "predictions": predicted,
        }

    result = {
        "protocol": {
            "object_cap_bytes": object_cap,
            "probe_cap_bytes": probe_cap,
            "bandwidth_mbps": args.bandwidth_mbps,
            "candidate_pipelines": CANDIDATES,
            "splits": {"train": [x[0] for x in DATASETS if x[1] == "train"], "validation": [x[0] for x in DATASETS if x[1] == "validation"], "test": [x[0] for x in DATASETS if x[1] == "test"]},
            "raw_bytes_processed": sum(int(row["raw_bytes"]) + int(row["probe_bytes"]) for row in rows),
        },
        "sfs_trace": sfs_trace,
        "selected_features": selected,
        "rows": rows,
        "validation_selected_features": summarize(validation, selected),
        "test_selected_features": summarize(test, selected),
        "test_all_features": summarize(test, CANDIDATES),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (args.output.with_suffix(".csv")).open("w", newline="", encoding="utf-8") as handle:
        fields = ["dataset", "predicted_pipeline", "oracle_pipeline", "relative_cr", "estimated_mlcomp_cost_s", "estimated_raw_cost_s", "estimated_zstd_cost_s"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["test_selected_features"]["predictions"])
    print(json.dumps({k: result[k] for k in ["protocol", "sfs_trace", "selected_features", "validation_selected_features", "test_selected_features", "test_all_features"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run a paired 10/100 Mbps CPSS bandwidth-sensitivity experiment.

Pass explicit ``NAME=PATH`` datasets pointing at the same subset manifest for
both bandwidths.  The two runs are deliberately separate result directories,
which preserves paired rows and avoids overwriting the existing measured
10/100 Mbps result.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dcc_experiment_driver import add_common_arguments, build_runner_command, execute, resolve_datasets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--bandwidths", default="10,100")
    parser.set_defaults(
        methods="raw,zstd-3,cpss-adaptive",
        probe_candidates="fast",
        repeats=3,
        adaptive_max_samples=2,
    )
    args = parser.parse_args()
    if not args.dataset:
        raise SystemExit("bandwidth sensitivity requires explicit --dataset NAME=PATH entries so both runs share one subset")
    try:
        bandwidths = [float(item) for item in str(args.bandwidths).split(",") if item.strip()]
    except ValueError as exc:
        raise SystemExit("--bandwidths must be comma-separated Mbps values") from exc
    if not bandwidths or any(value <= 0 for value in bandwidths):
        raise SystemExit("bandwidth values must be positive")
    datasets = resolve_datasets(args)
    root_result = Path(args.result_dir).expanduser().resolve()
    root_result.mkdir(parents=True, exist_ok=True)
    plan = {"bandwidths_mbps": bandwidths, "datasets": datasets, "methods": args.methods}
    (root_result / "bandwidth_matrix_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    statuses = []
    for bandwidth in bandwidths:
        child = argparse.Namespace(**vars(args))
        child.bandwidth_mbps = bandwidth
        label = str(bandwidth).replace(".", "p")
        child.result_dir = root_result / f"{label}mbps"
        child.run_id = f"{args.run_id}_{label}mbps"
        if args.dry_run:
            command = build_runner_command(child, datasets)
            print(json.dumps({"bandwidth_mbps": bandwidth, "command": command}, ensure_ascii=False, indent=2))
            statuses.append({"bandwidth_mbps": bandwidth, "status": "dry-run"})
            continue
        result = execute(child, datasets)
        statuses.append({"bandwidth_mbps": bandwidth, "returncode": result, "result_dir": str(child.result_dir)})
        if result != 0:
            (root_result / "bandwidth_matrix_status.json").write_text(json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return result
    (root_result / "bandwidth_matrix_status.json").write_text(json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

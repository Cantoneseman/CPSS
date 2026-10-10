#!/usr/bin/env python3
"""Run the CPSS sampling-budget sensitivity experiment.

Each budget is written to a separate result directory, so a failed budget can
be rerun without touching completed budgets.  The current server runner
supports budgets 1, 2, and 5; the script validates this explicitly instead of
silently pretending to measure unsupported budgets.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dcc_experiment_driver import add_common_arguments, build_runner_command, execute, resolve_datasets, write_launcher_metadata


SUPPORTED_BUDGETS = {1, 2, 5}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser)
    parser.add_argument("--budgets", default="1,2,5", help="comma-separated CPSS sample caps")
    parser.set_defaults(
        datasets="cesm_atm,miranda,noaa_gsod_csv",
        methods="raw,zstd-3,cpss-adaptive",
        probe_candidates="fast",
        repeats=3,
        bandwidth_mbps=100.0,
        adaptive_max_samples=2,
    )
    args = parser.parse_args()
    try:
        budgets = [int(item) for item in str(args.budgets).split(",") if item.strip()]
    except ValueError as exc:
        raise SystemExit("--budgets must be a comma-separated list of integers") from exc
    if not budgets or any(item not in SUPPORTED_BUDGETS for item in budgets):
        raise SystemExit(f"supported budgets are {sorted(SUPPORTED_BUDGETS)}; got {budgets}")
    if len(set(budgets)) != len(budgets):
        raise SystemExit("--budgets contains duplicates")

    datasets = resolve_datasets(args)
    root_result = Path(args.result_dir).expanduser().resolve()
    root_result.mkdir(parents=True, exist_ok=True)
    plan = {"budgets": budgets, "datasets": datasets, "methods": args.methods, "bandwidth_mbps": args.bandwidth_mbps}
    (root_result / "sampling_matrix_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    statuses = []
    for budget in budgets:
        child = argparse.Namespace(**vars(args))
        child.adaptive_max_samples = budget
        child.result_dir = root_result / f"budget_{budget}"
        child.run_id = f"{args.run_id}_budget{budget}"
        status_path = child.result_dir / "run_status.json"
        if status_path.is_file() and not args.dry_run:
            try:
                prior = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prior = {}
            if prior.get("status") == "completed" and int(prior.get("failed_rows", 1)) == 0:
                statuses.append({"budget": budget, "status": "already-completed", "result_dir": str(child.result_dir)})
                continue
        if args.dry_run:
            command = build_runner_command(child, datasets)
            print(json.dumps({"budget": budget, "command": command}, ensure_ascii=False, indent=2))
            statuses.append({"budget": budget, "status": "dry-run"})
            continue
        result = execute(child, datasets)
        statuses.append({"budget": budget, "returncode": result, "result_dir": str(child.result_dir)})
        if result != 0:
            (root_result / "sampling_matrix_status.json").write_text(json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return result
    (root_result / "sampling_matrix_status.json").write_text(json.dumps(statuses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

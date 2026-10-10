#!/usr/bin/env python3
"""Small, resumable launcher for the formal DCC GridFlux runner.

This module deliberately contains no experiment policy.  The three companion
scripts choose the workload, methods, and sampling budget; this file only
validates paths, builds the exact runner command, and records the command and
stdout/stderr next to the result directory.

The launcher is meant to be copied to the same checkout as
``Script/run_dcc_final_gridflux.py`` on the Shenzhen server.  ``--project-root``
can also point at another checkout, which keeps the local paper repository and
the server runner independent.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value).split(",") if part.strip()]


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    here = Path(__file__).resolve()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=here.parents[1],
        help="checkout containing Script/run_dcc_final_gridflux.py",
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        default=sys.executable,
        help="Python interpreter used for the formal runner",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="repeatable NAME=RAW_DIRECTORY; overrides --dataset-root",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        help="canonical DCCData/full-v1/datasets root (NAME/raw is appended)",
    )
    parser.add_argument(
        "--datasets",
        default="",
        help="comma-separated names used with --dataset-root",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--bandwidth-mbps", type=float, default=100.0)
    parser.add_argument("--methods", default="raw,zstd-3,cpss-adaptive")
    parser.add_argument("--probe-candidates", default="fast")
    parser.add_argument("--adaptive-max-samples", type=int, default=2)
    parser.add_argument("--adaptive-stability-cv", type=float, default=0.20)
    parser.add_argument("--random-seed", type=int, default=20260727)
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--resume-from", type=Path, default=None,
                        help="optional completed/partial formal-run directory to reuse")
    parser.add_argument("--remote-alias", default=None)
    parser.add_argument("--remote-host", default=None)
    parser.add_argument("--remote-project", default=None)
    parser.add_argument("--remote-python", default=None)
    parser.add_argument("--remote-root", default=None)
    parser.add_argument("--remote-server", default=None)
    parser.add_argument("--gridflux-client", default=None)
    parser.add_argument("--gridflux-port", type=int, default=None)
    parser.add_argument("--data-port-base", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")


def resolve_datasets(args: argparse.Namespace) -> list[str]:
    explicit = [str(item) for item in getattr(args, "dataset", [])]
    names = split_csv(getattr(args, "datasets", ""))
    root = getattr(args, "dataset_root", None)
    # The companion scripts provide a default name list for the usual
    # ``--dataset-root`` case.  Explicit NAME=PATH entries should therefore
    # override that default list; only an explicit root is contradictory.
    if explicit and root:
        raise SystemExit("use --dataset or --dataset-root, not both")
    if explicit:
        result = []
        for spec in explicit:
            if "=" not in spec:
                raise SystemExit(f"--dataset must be NAME=PATH: {spec}")
            name, raw_path = spec.split("=", 1)
            path = Path(raw_path).expanduser().resolve()
            if not name.strip() or not path.is_dir():
                raise SystemExit(f"dataset directory does not exist: {spec}")
            result.append(f"{name.strip()}={path}")
        return result
    if root is None:
        raise SystemExit("provide --dataset or --dataset-root together with --datasets")
    root = Path(root).expanduser().resolve()
    if not names:
        raise SystemExit("--datasets is required with --dataset-root")
    result = []
    for name in names:
        path = root / name / "raw"
        if not path.is_dir():
            raise SystemExit(f"missing dataset raw directory: {path}")
        result.append(f"{name}={path}")
    return result


def _optional_arg(args: argparse.Namespace, name: str, flag: str) -> list[str]:
    value = getattr(args, name, None)
    return [flag, str(value)] if value not in (None, "") else []


def build_runner_command(args: argparse.Namespace, datasets: Sequence[str]) -> list[str]:
    project_root = Path(args.project_root).expanduser().resolve()
    runner = project_root / "Script" / "run_dcc_final_gridflux.py"
    if not runner.is_file():
        raise SystemExit(
            "formal runner not found: "
            f"{runner}. Copy this launcher to the server checkout or pass --project-root."
        )
    result_dir = Path(args.result_dir).expanduser().resolve()
    if result_dir.exists() and not args.dry_run:
        # ``execute`` records its own launcher metadata before invoking the
        # formal runner.  Those two files are safe to keep; every other file
        # means that the destination is not a fresh run directory.
        allowed = {"launcher_config.json", "launcher_command.sh"}
        unexpected = [item.name for item in result_dir.iterdir() if item.name not in allowed]
        if unexpected:
            raise SystemExit(
                f"refusing to overwrite non-empty result directory: {result_dir} "
                f"(existing files: {', '.join(sorted(unexpected))})"
            )
    if args.repeats <= 0 or args.bandwidth_mbps <= 0:
        raise SystemExit("--repeats and --bandwidth-mbps must be positive")
    command = [
        str(args.python_executable),
        str(runner),
        "--methods",
        str(args.methods),
        "--repeats",
        str(args.repeats),
        "--high-repeats",
        str(args.repeats),
        "--high-repeat-datasets",
        "",
        "--bandwidth-bps",
        str(float(args.bandwidth_mbps) * 1_000_000.0),
        "--adaptive-max-samples",
        str(args.adaptive_max_samples),
        "--adaptive-stability-cv",
        str(args.adaptive_stability_cv),
        "--probe-candidates",
        str(args.probe_candidates),
        "--random-seed",
        str(args.random_seed),
        "--run-id",
        str(args.run_id),
        "--result-dir",
        str(result_dir),
    ]
    for dataset in datasets:
        command.extend(["--dataset", dataset])
    for name, flag in (
        ("remote_alias", "--remote-alias"),
        ("remote_host", "--remote-host"),
        ("remote_project", "--remote-project"),
        ("remote_python", "--remote-python"),
        ("remote_root", "--remote-root"),
        ("remote_server", "--remote-server"),
        ("gridflux_client", "--gridflux-client"),
        ("gridflux_port", "--gridflux-port"),
        ("data_port_base", "--data-port-base"),
    ):
        command.extend(_optional_arg(args, name, flag))
    if args.resume_from is not None:
        command.extend(["--resume-from", str(Path(args.resume_from).expanduser().resolve())])
    if args.dry_run:
        command.append("--dry-run")
    return command


def write_launcher_metadata(args: argparse.Namespace, datasets: Sequence[str], command: Sequence[str]) -> Path:
    result_dir = Path(args.result_dir).expanduser().resolve()
    result_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": utc_now(),
        "launcher": Path(__file__).resolve().name,
        "project_root": str(Path(args.project_root).expanduser().resolve()),
        "datasets": list(datasets),
        "command": list(command),
        "command_shell": shlex.join(str(item) for item in command),
    }
    path = result_dir / "launcher_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (result_dir / "launcher_command.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n" + shlex.join(str(item) for item in command) + "\n",
        encoding="utf-8",
    )
    return path


def execute(args: argparse.Namespace, datasets: Sequence[str] | None = None) -> int:
    datasets = list(datasets or resolve_datasets(args))
    command = build_runner_command(args, datasets)
    if args.dry_run:
        metadata = write_launcher_metadata(args, datasets, command)
        print(json.dumps({"metadata": str(metadata), "command": command}, ensure_ascii=False, indent=2))
        return 0
    result_dir = Path(args.result_dir).expanduser().resolve()
    # The formal runner requires a genuinely empty result directory at
    # startup.  Keep launcher output beside it and add metadata only after
    # the runner has finished creating its own result files.
    result_dir.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = result_dir.parent / f"{result_dir.name}.launcher_stdout.log"
    stderr_path = result_dir.parent / f"{result_dir.name}.launcher_stderr.log"
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            command,
            cwd=str(Path(args.project_root).expanduser().resolve()),
            stdout=stdout,
            stderr=stderr,
            text=True,
            env=env,
            check=False,
        )
    metadata = write_launcher_metadata(args, datasets, command)
    print(json.dumps({"metadata": str(metadata), "command": command}, ensure_ascii=False, indent=2))
    print(json.dumps({"returncode": completed.returncode, "result_dir": str(result_dir)}, ensure_ascii=False))
    return int(completed.returncode)

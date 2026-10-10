#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/root/projects/science-compressor")
REMOTE_ALIAS = "cloud-shanghai"
REMOTE_HOST = "139.196.252.29"
REMOTE_PROJECT = "/root/projects/science-compressor"
REMOTE_PYTHON = f"{REMOTE_PROJECT}/.venv/bin/python"
LOCAL_PYTHON = ROOT / ".venv" / "bin" / "python"
LOCAL_GRIDFLUX_CLIENT = "/root/projects/GridFlux/build-local/gridflux-file-client"
REMOTE_GRIDFLUX_SERVER = "/root/projects/GridFlux/build-cloud/gridflux-file-server"
GRIDFLUX_PORT = 25252
RATIO_TARGET = 3.0

RESULT_FIELDS = [
    "dataset",
    "unit_id",
    "source_file",
    "pipeline",
    "repeat",
    "original_size_bytes",
    "compressed_size_bytes",
    "compressed_fraction",
    "compression_ratio",
    "meets_ratio3",
    "compress_time_sec",
    "raw_gridflux_transfer_time_sec",
    "compressed_gridflux_transfer_time_sec",
    "shanghai_decompress_time_sec",
    "end_to_end_time_sec",
    "speedup_vs_raw_gridflux",
    "sha256_local",
    "sha256_shanghai_raw",
    "sha256_shanghai_restored",
    "sha256_ok",
    "status",
    "error",
]


@dataclass(frozen=True)
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float


@dataclass(frozen=True)
class PipelineSpec:
    name: str
    selection_reason: str


@dataclass(frozen=True)
class Unit:
    dataset: str
    unit_id: str
    source_file: Path
    dtype: str
    shape: str
    pipelines: tuple[PipelineSpec, ...]
    selection_reason: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_now() -> str:
    return datetime.now().astimezone().isoformat()


def quote(value: object) -> str:
    return shlex.quote(str(value))


def render_cmd(cmd: list[object]) -> str:
    return " ".join(quote(part) for part in cmd)


def sanitize(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text)


def build_gridflux_client_cmd(
    input_path: str,
    event_log: str,
    *,
    host: str = REMOTE_HOST,
    port: int = GRIDFLUX_PORT,
    connections: int = 4,
    chunk_size: int = 1_048_576,
    buffer_size: int = 1_048_576,
) -> list[str]:
    return [
        LOCAL_GRIDFLUX_CLIENT,
        "--host",
        host,
        "--port",
        str(port),
        "--input",
        input_path,
        "--connections",
        str(connections),
        "--chunk-size",
        str(chunk_size),
        "--buffer-size",
        str(buffer_size),
        "--checksum",
        "crc32c",
        "--checksum-backend",
        "auto",
        "--checksum-pipeline",
        "worker",
        "--event-log",
        event_log,
    ]


def build_gridflux_server_cmd(
    output_path: str,
    event_log: str,
    *,
    port: int = GRIDFLUX_PORT,
    connections: int = 4,
    buffer_size: int = 1_048_576,
) -> list[str]:
    return [
        REMOTE_GRIDFLUX_SERVER,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--output",
        output_path,
        "--connections",
        str(connections),
        "--buffer-size",
        str(buffer_size),
        "--checksum",
        "crc32c",
        "--checksum-backend",
        "auto",
        "--checksum-pipeline",
        "worker",
        "--event-log",
        event_log,
        "--overwrite",
    ]


def build_remote_server_start_shell(
    output_path: str,
    event_log: str,
    stdout_path: str,
    stderr_path: str,
    exit_path: str,
    pid_path: str,
) -> str:
    server_cmd = build_gridflux_server_cmd(output_path, event_log)
    cleanup = "rm -f -- " + " ".join(
        quote(path) for path in (output_path, event_log, stdout_path, stderr_path, exit_path, pid_path)
    )
    remote_dir = os.path.dirname(output_path)
    wrapped = (
        f"({render_cmd(server_cmd)} > {quote(stdout_path)} 2> {quote(stderr_path)}; "
        f"printf '%s\\n' \"$?\" > {quote(exit_path)}) & printf '%s\\n' \"$!\" > {quote(pid_path)}"
    )
    return f"set -e; mkdir -p {quote(remote_dir)}; {cleanup}; {wrapped}"


class Runner:
    def __init__(self, run_dir: Path, repeats: int, dry_run: bool = False) -> None:
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.repeats = repeats
        self.dry_run = dry_run
        self.log_dir = run_dir / "logs"
        self.tmp_dir = run_dir / "artifacts" / "tmp"
        self.results_path = run_dir / "results.csv"
        self.summary_path = run_dir / "summary.md"
        self.dataset_selection_path = run_dir / "dataset_selection.md"
        self.environment_path = run_dir / "environment.json"
        self.command_log_path = run_dir / "command_log.txt"
        self.sha_log_path = run_dir / "sha256_checks.txt"
        self.status_path = run_dir / "status.json"
        self.resume_path = run_dir / "RESUME.md"
        self.remote_tmp = f"/tmp/{self.run_id}"

    def ensure_dirs(self) -> None:
        for path in (self.run_dir, self.log_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)

    def append_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)

    def write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_resume_state(
        self,
        *,
        phase: str,
        status: str,
        next_action: str,
        blocker: str | None = None,
    ) -> None:
        payload = {
            "run_id": self.run_id,
            "updated_at_utc": utc_now(),
            "phase": phase,
            "status": status,
            "blocker": blocker or "",
            "next_action": next_action,
            "goal": "Run the Shenzhen -> Shanghai public-network GridFlux experiment for high-ratio scientific datasets.",
            "project_root": str(ROOT),
            "run_dir": str(self.run_dir),
            "remote_host": REMOTE_HOST,
            "remote_alias": REMOTE_ALIAS,
            "gridflux_port": GRIDFLUX_PORT,
            "status_file": str(self.status_path),
            "resume_file": str(self.resume_path),
            "summary_file": str(self.summary_path),
            "results_file": str(self.results_path),
            "command_log_file": str(self.command_log_path),
            "hard_rules": [
                "Use GridFlux as the data channel; do not use scp/rsync for main metrics.",
                "Use public IP 139.196.252.29; do not use 192.168.10.x private addresses.",
                "Do not start the formal experiment until the 1 MiB GridFlux smoke test passes.",
                "Do not test AI training datasets in this round.",
            ],
        }
        self.write_json(self.status_path, payload)

        lines = [
            "# Context Compaction Recovery",
            "",
            "If the Codex context is compacted or resumed, continue this same GridFlux experiment and do not ask for a new task.",
            "",
            "## Current State",
            "",
            f"- run_id: `{self.run_id}`",
            f"- phase: `{phase}`",
            f"- status: `{status}`",
            f"- blocker: `{blocker or ''}`",
            f"- next_action: {next_action}",
            "",
            "## Experiment Anchor",
            "",
            "- Goal: run the Shenzhen -> Shanghai public-network GridFlux experiment for high-ratio scientific datasets.",
            f"- Project root: `{ROOT}`",
            f"- Run directory: `{self.run_dir}`",
            f"- Shanghai public GridFlux endpoint: `{REMOTE_HOST}:{GRIDFLUX_PORT}`",
            f"- Remote SSH alias: `{REMOTE_ALIAS}`",
            "- First required action after resume: read `status.json`, `summary.md`, and `command_log.txt`, then continue from `next_action`.",
            "",
            "## Hard Rules",
            "",
            "- Use GridFlux for raw and compressed transfers; scp/rsync cannot be main result metrics.",
            "- Use the Shanghai public IP, not private `192.168.10.x` addresses.",
            "- If the public GridFlux port is unreachable, stop and report the blocker.",
            "- Only after the 1 MiB smoke test passes may the formal dataset experiment start.",
        ]
        self.resume_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def run_cmd(
        self,
        cmd: list[object],
        *,
        cwd: Path | None = ROOT,
        timeout: int | None = None,
        log_name: str | None = None,
    ) -> CommandResult:
        rendered = render_cmd(cmd)
        if self.dry_run:
            result = CommandResult([str(item) for item in cmd], 0, f"DRY_RUN: {rendered}\n", "", 0.0)
            self.log_command(result, cwd, log_name)
            return result

        start = time.perf_counter()
        started = utc_now()
        try:
            proc = subprocess.run(
                [str(item) for item in cmd],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            stderr = f"{stderr}\nTIMEOUT after {timeout} seconds"
            returncode = 124
        elapsed = time.perf_counter() - start
        result = CommandResult([str(item) for item in cmd], returncode, stdout, stderr, elapsed)
        self.log_command(result, cwd, log_name, started=started)
        return result

    def log_command(
        self,
        result: CommandResult,
        cwd: Path | None,
        log_name: str | None,
        *,
        started: str | None = None,
    ) -> None:
        entry = "\n".join(
            [
                f"## {started or utc_now()}",
                f"$ {render_cmd(result.cmd)}",
                f"cwd={cwd}",
                f"returncode={result.returncode}",
                f"elapsed_s={result.elapsed_s:.6f}",
                "[stdout]",
                result.stdout.rstrip(),
                "[stderr]",
                result.stderr.rstrip(),
                "",
            ]
        )
        self.append_text(self.command_log_path, entry)
        if log_name:
            (self.log_dir / log_name).write_text(entry, encoding="utf-8")

    def ssh_cmd(self, remote_shell: str, *, timeout: int | None = None, log_name: str | None = None) -> CommandResult:
        return self.run_cmd(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=8",
                "-o",
                "StrictHostKeyChecking=accept-new",
                REMOTE_ALIAS,
                remote_shell,
            ],
            timeout=timeout,
            log_name=log_name,
        )

    def init_results(self) -> None:
        with self.results_path.open("w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=RESULT_FIELDS).writeheader()

    def append_result(self, row: dict[str, Any]) -> None:
        with self.results_path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS)
            writer.writerow({field: row.get(field, "") for field in RESULT_FIELDS})

    def selected_units(self) -> list[Unit]:
        prepared = ROOT / "ExperimentRecords" / "huawei_t4_hpc_ai_5gb_20260602_233623" / "datasets" / "prepared"
        hurricane = prepared / "hpc_sdrbench_hurricane_isabel_100x500x500"
        cesm = prepared / "hpc_sdrbench_cesm_atm_1800x3600"
        return [
            Unit(
                "Hurricane-ISABEL",
                "hurricane_cloud_log10",
                hurricane / "100x500x500__CLOUDf48.log10.bin.f32",
                "float32",
                "100,500,500",
                (PipelineSpec("xz_lzma2", "previous lossless ratio 10.188 >= 3.0"),),
                "ratio target candidate; previous lossless run reproduced author ratio closely",
            ),
            Unit(
                "Hurricane-ISABEL",
                "hurricane_qice_log10",
                hurricane / "100x500x500__QICEf48.log10.bin.f32",
                "float32",
                "100,500,500",
                (PipelineSpec("xz_lzma2", "previous lossless ratio 12.902 >= 3.0"),),
                "ratio target candidate; previous lossless run exceeded 3:1",
            ),
            Unit(
                "CESM-ATM",
                "cesm_fsdtoa",
                cesm / "1800x3600__FSDTOA_1_1800_3600.f32",
                "float32",
                "1800,3600",
                (
                    PipelineSpec("higher_order_delta->xz_lzma2", "previous lossless ratio 7.262 >= 3.0"),
                    PipelineSpec("tdt->xz_lzma2", "previous lossless ratio 3.712 >= 3.0"),
                ),
                "ratio target candidate; both previous lossless pipelines exceeded 3:1",
            ),
        ]

    def write_dataset_selection(self, units: list[Unit]) -> None:
        lines = [
            "# Dataset Selection",
            "",
            f"- run_id: `{self.run_id}`",
            f"- hard_ratio_target: `compression_ratio >= {RATIO_TARGET}`",
            "- transfer_channel: `GridFlux public TCP 25252`",
            "- policy: only repeat existing lossless candidates that previously reached 3:1; do not download new data in this round.",
            "",
            "## Selected",
            "",
        ]
        for unit in units:
            lines.extend(
                [
                    f"### {unit.dataset} / {unit.unit_id}",
                    f"- path: `{unit.source_file}`",
                    f"- exists: `{unit.source_file.exists()}`",
                    f"- size_bytes: `{unit.source_file.stat().st_size if unit.source_file.exists() else ''}`",
                    f"- dtype: `{unit.dtype}`",
                    f"- shape: `{unit.shape}`",
                    f"- pipelines: `{', '.join(pipe.name for pipe in unit.pipelines)}`",
                    f"- reason: {unit.selection_reason}",
                    "",
                ]
            )
        self.dataset_selection_path.write_text("\n".join(lines), encoding="utf-8")

    def sha256_file(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def remote_sha256(self, remote_path: str, log_name: str) -> str | None:
        res = self.ssh_cmd(f"sha256sum {quote(remote_path)} | awk '{{print $1}}'", timeout=3600, log_name=log_name)
        if res.returncode != 0:
            return None
        lines = res.stdout.strip().splitlines()
        return lines[-1].strip() if lines else None

    def remote_cleanup(self, *paths: str) -> None:
        if paths:
            self.ssh_cmd("rm -f -- " + " ".join(quote(path) for path in paths), timeout=3600)

    def wait_remote_listen(self, timeout_s: float = 20.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res = self.ssh_cmd(f"ss -ltn | grep -q ':{GRIDFLUX_PORT} '", timeout=10)
            if res.returncode == 0:
                return True
            time.sleep(0.5)
        return False

    def wait_remote_exit(self, exit_path: str, timeout_s: float = 3600.0) -> int | None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res = self.ssh_cmd(f"test -f {quote(exit_path)} && cat {quote(exit_path)} || true", timeout=10)
            value = res.stdout.strip().splitlines()
            if value:
                try:
                    return int(value[-1])
                except ValueError:
                    return None
            time.sleep(0.5)
        return None

    def collect_remote_log(self, remote_path: str, local_name: str) -> None:
        res = self.ssh_cmd(f"cat {quote(remote_path)} 2>/dev/null || true", timeout=60)
        (self.log_dir / local_name).write_text(res.stdout, encoding="utf-8")

    def gridflux_transfer(self, local_input: Path, remote_output: str, tag: str) -> tuple[float | None, str]:
        event_remote = f"{remote_output}.server_events.jsonl"
        stdout_remote = f"{remote_output}.server_stdout.log"
        stderr_remote = f"{remote_output}.server_stderr.log"
        exit_remote = f"{remote_output}.server_exit"
        pid_remote = f"{remote_output}.server_pid"
        event_local = self.log_dir / f"{tag}_client_events.jsonl"
        start_shell = build_remote_server_start_shell(
            remote_output, event_remote, stdout_remote, stderr_remote, exit_remote, pid_remote
        )
        start_res = self.ssh_cmd(start_shell, timeout=60, log_name=f"{tag}_server_start.log")
        if start_res.returncode != 0:
            return None, f"server start failed: {start_res.stderr or start_res.stdout}"
        if not self.wait_remote_listen():
            self.collect_remote_log(stderr_remote, f"{tag}_server_stderr.log")
            return None, "server did not listen on GridFlux port"

        client_res = self.run_cmd(
            build_gridflux_client_cmd(str(local_input), str(event_local)),
            timeout=6 * 60 * 60,
            log_name=f"{tag}_gridflux_client.log",
        )
        server_code = self.wait_remote_exit(exit_remote)
        self.collect_remote_log(stdout_remote, f"{tag}_server_stdout.log")
        self.collect_remote_log(stderr_remote, f"{tag}_server_stderr.log")
        self.collect_remote_log(event_remote, f"{tag}_server_events.jsonl")
        if client_res.returncode != 0:
            self.remote_cleanup(remote_output)
            return None, f"client failed: {client_res.stderr or client_res.stdout}"
        if server_code != 0:
            self.remote_cleanup(remote_output)
            return None, f"server exit code: {server_code}"
        return client_res.elapsed_s, ""

    def compress_local(self, unit: Unit, pipeline: PipelineSpec, output_path: Path, repeat: int) -> CommandResult:
        return self.run_cmd(
            [
                str(LOCAL_PYTHON),
                "-m",
                "Compressor.cli",
                "compress",
                str(unit.source_file),
                "-o",
                str(output_path),
                "--selector",
                "exhaustive",
                "--pipelines",
                pipeline.name,
                "--dtype",
                unit.dtype,
                "--shape",
                unit.shape,
            ],
            timeout=6 * 60 * 60,
            log_name=f"{unit.unit_id}__{sanitize(pipeline.name)}__rep{repeat}_compress.log",
        )

    def decompress_remote(self, remote_cpss: str, remote_restored: str, unit: Unit, pipeline: PipelineSpec, repeat: int) -> CommandResult:
        return self.ssh_cmd(
            f"cd {quote(REMOTE_PROJECT)} && {quote(REMOTE_PYTHON)} -m Compressor.cli decompress {quote(remote_cpss)} -o {quote(remote_restored)}",
            timeout=6 * 60 * 60,
            log_name=f"{unit.unit_id}__{sanitize(pipeline.name)}__rep{repeat}_remote_decompress.log",
        )

    def smoke_test(self) -> bool:
        smoke_local = self.tmp_dir / "gridflux_smoke_send_1mib.bin"
        smoke_local.write_bytes(os.urandom(1024 * 1024))
        local_sha = self.sha256_file(smoke_local)
        remote_output = f"{self.remote_tmp}/gridflux_smoke_recv_1mib.bin"
        elapsed, error = self.gridflux_transfer(smoke_local, remote_output, "smoke_1mib")
        remote_sha = self.remote_sha256(remote_output, "smoke_1mib_remote_sha256.log") if elapsed is not None else None
        ok = elapsed is not None and remote_sha == local_sha
        self.append_text(
            self.sha_log_path,
            f"{utc_now()},smoke,local,{local_sha},{smoke_local}\n{utc_now()},smoke,remote,{remote_sha},{remote_output},ok={ok},error={error}\n",
        )
        self.remote_cleanup(remote_output)
        return ok

    def probe_environment(self, units: list[Unit]) -> None:
        local_gridflux = self.run_cmd([LOCAL_GRIDFLUX_CLIENT, "--help"], timeout=30, log_name="local_gridflux_help.log")
        remote_probe = self.ssh_cmd(
            f"hostname; test -x {quote(REMOTE_GRIDFLUX_SERVER)} && echo GRIDFLUX_SERVER_OK; "
            f"test -x {quote(REMOTE_PYTHON)} && echo REMOTE_PYTHON_OK; "
            f"ss -ltn | grep -q ':{GRIDFLUX_PORT} ' && echo PORT_BUSY || echo PORT_FREE",
            timeout=60,
            log_name="remote_probe.log",
        )
        self.write_json(
            self.environment_path,
            {
                "run_id": self.run_id,
                "created_at_utc": utc_now(),
                "created_at_local": local_now(),
                "ratio_target": RATIO_TARGET,
                "transfer_channel": "GridFlux",
                "gridflux_host": REMOTE_HOST,
                "gridflux_port": GRIDFLUX_PORT,
                "local": {
                    "hostname": socket.gethostname(),
                    "platform": platform.platform(),
                    "project_root": str(ROOT),
                    "disk_root": shutil.disk_usage("/")._asdict(),
                },
                "local_gridflux_help": {
                    "returncode": local_gridflux.returncode,
                    "stdout": local_gridflux.stdout,
                    "stderr": local_gridflux.stderr,
                },
                "remote_probe": {
                    "returncode": remote_probe.returncode,
                    "stdout": remote_probe.stdout,
                    "stderr": remote_probe.stderr,
                },
                "selected_units": [
                    {
                        "dataset": unit.dataset,
                        "unit_id": unit.unit_id,
                        "path": str(unit.source_file),
                        "exists": unit.source_file.exists(),
                        "size_bytes": unit.source_file.stat().st_size if unit.source_file.exists() else None,
                        "pipelines": [pipe.name for pipe in unit.pipelines],
                    }
                    for unit in units
                ],
            },
        )

    def run_roundtrip_diagnostics(self) -> None:
        diag_path = self.run_dir / "shuffle_roundtrip_diagnostics.md"
        prepared = ROOT / "ExperimentRecords" / "huawei_t4_hpc_ai_5gb_20260602_233623" / "datasets" / "prepared"
        source = prepared / "hpc_sdrbench_hurricane_isabel_100x500x500" / "100x500x500__CLOUDf48.log10.bin.f32"
        cpss = self.tmp_dir / "diagnostic_hurricane_cloud_shuffle.cpss"
        restored = self.tmp_dir / "diagnostic_hurricane_cloud_shuffle.restored"
        lines = [
            "# Shuffle Roundtrip Diagnostics",
            "",
            "- scope: local-only diagnostic; not part of GridFlux transfer metrics.",
            f"- source: `{source}`",
            "- pipeline: `shuffle->xz_lzma2`",
            "",
        ]
        if not source.exists():
            lines.append("- status: source missing")
            diag_path.write_text("\n".join(lines), encoding="utf-8")
            return
        original_sha = self.sha256_file(source)
        compress_res = self.run_cmd(
            [
                str(LOCAL_PYTHON),
                "-m",
                "Compressor.cli",
                "compress",
                str(source),
                "-o",
                str(cpss),
                "--selector",
                "exhaustive",
                "--pipelines",
                "shuffle->xz_lzma2",
                "--dtype",
                "float32",
                "--shape",
                "100,500,500",
            ],
            timeout=6 * 60 * 60,
            log_name="diagnostic_shuffle_compress.log",
        )
        if compress_res.returncode != 0 or not cpss.exists():
            lines.extend(["- status: compress failed", "", "```text", compress_res.stderr or compress_res.stdout, "```"])
            diag_path.write_text("\n".join(lines), encoding="utf-8")
            return
        decompress_res = self.run_cmd(
            [str(LOCAL_PYTHON), "-m", "Compressor.cli", "decompress", str(cpss), "-o", str(restored)],
            timeout=6 * 60 * 60,
            log_name="diagnostic_shuffle_decompress.log",
        )
        restored_sha = self.sha256_file(restored) if restored.exists() else None
        lines.extend(
            [
                f"- compress_returncode: `{compress_res.returncode}`",
                f"- decompress_returncode: `{decompress_res.returncode}`",
                f"- original_sha256: `{original_sha}`",
                f"- restored_sha256: `{restored_sha}`",
                f"- sha256_ok: `{original_sha == restored_sha}`",
                f"- restored_size: `{restored.stat().st_size if restored.exists() else ''}`",
                "",
                "Interpretation: if `sha256_ok` is false locally, the shuffle pipeline is not eligible for lossless ratio claims until the reversible transform is fixed.",
            ]
        )
        diag_path.write_text("\n".join(lines), encoding="utf-8")
        cpss.unlink(missing_ok=True)
        restored.unlink(missing_ok=True)

    def run_experiment(self) -> list[dict[str, Any]]:
        self.ensure_dirs()
        self.write_resume_state(
            phase="initializing",
            status="running",
            next_action="Probe local and Shanghai environments, then run the 1 MiB GridFlux smoke test.",
        )
        units = self.selected_units()
        self.write_dataset_selection(units)
        self.init_results()
        self.probe_environment(units)
        if self.dry_run:
            self.write_resume_state(
                phase="dry_run",
                status="complete",
                next_action="Dry run finished. Run without --dry-run after confirming the Shanghai public GridFlux port is open.",
            )
            self.write_summary([])
            return []
        self.ssh_cmd(f"mkdir -p {quote(self.remote_tmp)}", timeout=3600, log_name="remote_prepare_tmp.log")
        self.write_resume_state(
            phase="smoke_test",
            status="running",
            next_action="Run the 1 MiB GridFlux smoke test against 139.196.252.29:25252.",
        )
        if not self.smoke_test():
            self.write_resume_state(
                phase="smoke_test",
                status="blocked",
                blocker="GridFlux smoke test failed",
                next_action="Open Shanghai public TCP port 25252 for Shenzhen, then rerun the 1 MiB GridFlux smoke test.",
            )
            self.write_summary([], blocker="GridFlux smoke test failed")
            return []

        self.write_resume_state(
            phase="formal_experiment",
            status="running",
            next_action="Run raw GridFlux baselines and compressed GridFlux transfers for the selected scientific datasets.",
        )
        rows: list[dict[str, Any]] = []
        for unit in units:
            if not unit.source_file.exists():
                for pipeline in unit.pipelines:
                    row = {
                        "dataset": unit.dataset,
                        "unit_id": unit.unit_id,
                        "source_file": str(unit.source_file),
                        "pipeline": pipeline.name,
                        "status": "failed",
                        "error": "source file missing",
                    }
                    self.append_result(row)
                    rows.append(row)
                continue
            original_size = unit.source_file.stat().st_size
            local_sha = self.sha256_file(unit.source_file)
            raw_by_repeat: dict[int, tuple[float | None, str | None]] = {}
            for repeat in range(1, self.repeats + 1):
                remote_raw = f"{self.remote_tmp}/{unit.unit_id}__rep{repeat}__raw.bin"
                raw_elapsed, raw_error = self.gridflux_transfer(
                    unit.source_file, remote_raw, f"{unit.unit_id}__raw_rep{repeat}"
                )
                remote_raw_sha = self.remote_sha256(remote_raw, f"{unit.unit_id}__raw_rep{repeat}_sha256.log") if raw_elapsed is not None else None
                self.append_text(
                    self.sha_log_path,
                    f"{utc_now()},{unit.unit_id},raw,rep{repeat},local={local_sha},remote={remote_raw_sha},ok={local_sha == remote_raw_sha}\n",
                )
                raw_by_repeat[repeat] = (raw_elapsed, remote_raw_sha)
                self.remote_cleanup(remote_raw)
                if raw_elapsed is None or remote_raw_sha != local_sha:
                    self.append_text(self.command_log_path, f"RAW_BASELINE_FAILED {unit.unit_id} rep{repeat}: {raw_error}\n")

            for pipeline in unit.pipelines:
                for repeat in range(1, self.repeats + 1):
                    tag = f"{unit.unit_id}__{sanitize(pipeline.name)}__rep{repeat}"
                    compressed_path = self.tmp_dir / f"{tag}.cpss"
                    compressed_path.unlink(missing_ok=True)
                    compress_res = self.compress_local(unit, pipeline, compressed_path, repeat)
                    base_row: dict[str, Any] = {
                        "dataset": unit.dataset,
                        "unit_id": unit.unit_id,
                        "source_file": str(unit.source_file),
                        "pipeline": pipeline.name,
                        "repeat": repeat,
                        "original_size_bytes": original_size,
                        "compress_time_sec": round(compress_res.elapsed_s, 6),
                        "raw_gridflux_transfer_time_sec": round(raw_by_repeat.get(repeat, (None, None))[0], 6)
                        if raw_by_repeat.get(repeat, (None, None))[0] is not None
                        else "",
                        "sha256_local": local_sha,
                        "sha256_shanghai_raw": raw_by_repeat.get(repeat, (None, None))[1] or "",
                    }
                    if compress_res.returncode != 0 or not compressed_path.exists():
                        row = {
                            **base_row,
                            "sha256_ok": False,
                            "status": "failed",
                            "error": (compress_res.stderr or compress_res.stdout or "compression failed").strip(),
                        }
                        self.append_result(row)
                        rows.append(row)
                        continue
                    compressed_size = compressed_path.stat().st_size
                    ratio = original_size / compressed_size
                    remote_cpss = f"{self.remote_tmp}/{compressed_path.name}"
                    remote_restored = f"{remote_cpss}.restored"
                    transfer_elapsed, transfer_error = self.gridflux_transfer(compressed_path, remote_cpss, tag)
                    if transfer_elapsed is None:
                        row = {
                            **base_row,
                            "compressed_size_bytes": compressed_size,
                            "compressed_fraction": compressed_size / original_size,
                            "compression_ratio": ratio,
                            "meets_ratio3": ratio >= RATIO_TARGET,
                            "sha256_ok": False,
                            "status": "failed",
                            "error": transfer_error,
                        }
                        self.append_result(row)
                        rows.append(row)
                        compressed_path.unlink(missing_ok=True)
                        continue
                    decomp_res = self.decompress_remote(remote_cpss, remote_restored, unit, pipeline, repeat)
                    restored_sha = self.remote_sha256(remote_restored, f"{tag}_restored_sha256.log") if decomp_res.returncode == 0 else None
                    sha_ok = local_sha == restored_sha
                    raw_elapsed = raw_by_repeat.get(repeat, (None, None))[0]
                    e2e = compress_res.elapsed_s + transfer_elapsed + decomp_res.elapsed_s
                    row = {
                        **base_row,
                        "compressed_size_bytes": compressed_size,
                        "compressed_fraction": compressed_size / original_size,
                        "compression_ratio": ratio,
                        "meets_ratio3": ratio >= RATIO_TARGET,
                        "compressed_gridflux_transfer_time_sec": round(transfer_elapsed, 6),
                        "shanghai_decompress_time_sec": round(decomp_res.elapsed_s, 6),
                        "end_to_end_time_sec": round(e2e, 6),
                        "speedup_vs_raw_gridflux": round(raw_elapsed / e2e, 6) if raw_elapsed else "",
                        "sha256_shanghai_restored": restored_sha or "",
                        "sha256_ok": sha_ok,
                        "status": "ok" if sha_ok else "failed",
                        "error": "" if sha_ok else (decomp_res.stderr or decomp_res.stdout or "sha256 mismatch").strip(),
                    }
                    self.append_result(row)
                    rows.append(row)
                    self.append_text(
                        self.sha_log_path,
                        f"{utc_now()},{unit.unit_id},{pipeline.name},rep{repeat},local={local_sha},restored={restored_sha},ok={sha_ok}\n",
                    )
                    compressed_path.unlink(missing_ok=True)
                    self.remote_cleanup(remote_cpss, remote_restored)
        self.run_roundtrip_diagnostics()
        self.ssh_cmd(f"rm -rf -- {quote(self.remote_tmp)}", timeout=3600, log_name="remote_final_cleanup.log")
        self.write_summary(rows)
        self.write_resume_state(
            phase="formal_experiment",
            status="complete",
            next_action="Review summary.md, results.csv, command_log.txt, and sha256_checks.txt.",
        )
        return rows

    def write_summary(self, rows: list[dict[str, Any]], blocker: str | None = None) -> None:
        ok_rows = [r for r in rows if r.get("status") == "ok" and str(r.get("sha256_ok")) == "True"]
        passing = [r for r in ok_rows if float(r.get("compression_ratio") or 0) >= RATIO_TARGET]
        lines = [
            "# Ratio>=3 GridFlux Public Experiment Summary",
            "",
            f"- run_id: `{self.run_id}`",
            f"- hard metric: `compression_ratio >= {RATIO_TARGET}`",
            "- transfer channel: `GridFlux`, not scp/rsync",
            f"- repeats: `{self.repeats}`",
            f"- result_rows: `{len(rows)}`",
            f"- lossless_ok_rows: `{len(ok_rows)}`",
            f"- ratio3_lossless_rows: `{len(passing)}`",
        ]
        if blocker:
            lines.extend(["", "## Blocker", "", f"- {blocker}"])
            self.summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return
        lines.extend(["", "## Ratio Target Results", ""])
        if not rows:
            lines.append("- No formal rows generated.")
        else:
            for row in rows:
                ratio = row.get("compression_ratio", "")
                meets = row.get("meets_ratio3", "")
                sha_ok = row.get("sha256_ok", "")
                status = row.get("status", "")
                lines.append(
                    f"- {row.get('unit_id')} `{row.get('pipeline')}` rep{row.get('repeat')}: "
                    f"ratio={float(ratio):.3f} if ratio else ratio, meets3={meets}, sha256={sha_ok}, status={status}, "
                    f"compress={row.get('compress_time_sec')}, gridflux_cpss={row.get('compressed_gridflux_transfer_time_sec')}, "
                    f"decompress={row.get('shanghai_decompress_time_sec')}, speedup={row.get('speedup_vs_raw_gridflux')}"
                )
        lines.extend(["", "## Interpretation", ""])
        lines.append(
            "Only rows with `status=ok`, `sha256_ok=True`, and `compression_ratio >= 3.0` satisfy the project hard metric."
        )
        self.summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def default_run_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "ExperimentRecords" / f"ratio3_gridflux_public_{stamp}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ratio>=3 Shenzhen -> Shanghai GridFlux public experiment.")
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir or default_run_dir()
    runner = Runner(run_dir=run_dir, repeats=args.repeats, dry_run=args.dry_run)
    runner.run_experiment()
    print(run_dir)


if __name__ == "__main__":
    main()

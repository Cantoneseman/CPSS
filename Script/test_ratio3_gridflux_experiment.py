import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_ratio3_gridflux_experiment as runner


class GridFluxCommandTest(unittest.TestCase):
    def test_client_command_uses_gridflux_binary_and_not_scp(self):
        cmd = runner.build_gridflux_client_cmd("/tmp/input.bin", "/tmp/client.jsonl")
        rendered = " ".join(cmd)
        self.assertIn("gridflux-file-client", cmd[0])
        self.assertIn("--host", cmd)
        self.assertIn("139.196.252.29", cmd)
        self.assertIn("--port", cmd)
        self.assertIn("25252", cmd)
        self.assertNotIn("scp", rendered)

    def test_server_command_uses_gridflux_binary_and_not_scp(self):
        cmd = runner.build_gridflux_server_cmd("/tmp/output.bin", "/tmp/server.jsonl")
        rendered = " ".join(cmd)
        self.assertIn("gridflux-file-server", cmd[0])
        self.assertIn("--host", cmd)
        self.assertIn("0.0.0.0", cmd)
        self.assertIn("--port", cmd)
        self.assertIn("25252", cmd)
        self.assertNotIn("scp", rendered)

    def test_remote_start_shell_records_exit_status(self):
        shell = runner.build_remote_server_start_shell(
            "/tmp/out.bin",
            "/tmp/server.jsonl",
            "/tmp/stdout.log",
            "/tmp/stderr.log",
            "/tmp/exit",
            "/tmp/pid",
        )
        self.assertIn("gridflux-file-server", shell)
        self.assertIn("/tmp/exit", shell)
        self.assertIn("/tmp/pid", shell)
        self.assertNotIn(" scp ", shell)

    def test_resume_state_writes_status_and_resume_guide(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "ratio3_gridflux_public_test"
            subject = runner.Runner(run_dir=run_dir, repeats=1, dry_run=True)
            subject.ensure_dirs()

            subject.write_resume_state(
                phase="smoke_test",
                status="blocked",
                blocker="GridFlux smoke test failed",
                next_action="Open Shanghai public TCP port 25252, then rerun the 1 MiB GridFlux smoke test.",
            )

            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            resume = (run_dir / "RESUME.md").read_text(encoding="utf-8")

            self.assertEqual(status["phase"], "smoke_test")
            self.assertEqual(status["status"], "blocked")
            self.assertEqual(status["blocker"], "GridFlux smoke test failed")
            self.assertIn("Open Shanghai public TCP port 25252", status["next_action"])
            self.assertIn("Context Compaction Recovery", resume)
            self.assertIn("do not ask for a new task", resume)
            self.assertIn("GridFlux smoke test failed", resume)
            self.assertIn("139.196.252.29", resume)
            self.assertIn("25252", resume)

    def test_shenzhen_prompt_includes_context_compaction_recovery_anchor(self):
        prompt_path = (
            Path(__file__).resolve().parents[1]
            / "ExperimentRecords"
            / "cross_domain_high_ratio_science_gridflux_public_20260603_preflight"
            / "SHENZHEN_CODEX_PROMPT.md"
        )
        if not prompt_path.exists():
            self.skipTest(f"historical prompt fixture is not present: {prompt_path}")
        prompt = prompt_path.read_text(encoding="utf-8")

        self.assertIn("Context Compaction Recovery", prompt)
        self.assertIn("RESUME.md", prompt)
        self.assertIn("status.json", prompt)
        self.assertIn("do not ask for a new task", prompt)


if __name__ == "__main__":
    unittest.main()

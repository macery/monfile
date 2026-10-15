"""Folder trigger mode and the command action (DESIGN §4.6, §6.4, ADR-0011)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from helpers import TreeCase

from monfile.actions import command
from monfile.batch import BUSY_RETRY_SECONDS, HELD, IDLE, PENDING, SETTLING
from monfile.cli import _retry_batch
from monfile.config import ConfigError
from monfile.runner import Runner
from monfile.state import StateStore


class BatchCase(TreeCase):
    def setUp(self) -> None:
        super().setUp()
        # Test files belong to the test user under a temp root; production trusts only root from "/".
        for name, value in (("TRUSTED_OWNERS", frozenset({0, os.getuid()})), ("TRUST_ROOT", self.root)):
            p = mock.patch.object(command, name, value)
            p.start()
            self.addCleanup(p.stop)
        (self.root / "bin").mkdir(mode=0o755)
        os.chmod(self.root / "bin", 0o755)

    def program(self, body: str, name: str = "prog.sh") -> Path:
        p = self.root / "bin" / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        os.chmod(p, 0o755)
        return p

    def batch_folder(self, program: Path, **params: Any) -> dict[str, Any]:
        return {"trigger": "folder", "recursive": True, "manifest": True,
                "action": [{"type": "command", "program": str(program), **params}]}

    def runner(self, folder: dict[str, Any], *, dry_run: bool = False, allow_commands: bool = True) -> Runner:
        r = Runner(self.config(folder), self.store(), dry_run=dry_run, allow_commands=allow_commands)
        r.startup(now=0)
        return r

    def finish(self, r: Runner, start: float) -> float:
        """Keep cycling until the running command exits. Returns the last 'now' used."""
        t = start
        deadline = time.monotonic() + 15
        while r.batches.runs and time.monotonic() < deadline:
            time.sleep(0.05)
            t += 0.05
            r.run_cycle(now=t)
        self.assertEqual(r.batches.runs, {}, "command did not finish")
        return t

    def state(self, r: Runner) -> str:
        bf = r.store.get_batch_folder("t")
        return bf.status if bf else "none"


class TestBatchConfig(BatchCase):
    def test_valid_batch_folder(self) -> None:
        cfg = self.config(self.batch_folder(self.program("true"), timeout_seconds=60, busy_exit_code=75))
        f = cfg.folders[0]
        self.assertEqual((f.trigger, f.verify_hash, f.consumes_source, f.output_roots()), ("folder", False, True, []))

    def test_rules(self) -> None:
        prog = str(self.program("true"))
        cases = [
            ({"action": [{"type": "command", "program": prog}]}, "requires trigger = \"folder\""),
            ({"trigger": "folder"}, "exactly one [[folder.action]], of type \"command\""),
            ({**self.batch_folder(Path(prog)), "verify_hash": True}, "verify_hash is not used"),
            ({**self.batch_folder(Path(prog)), "ready_marker": ".done"}, "ready_marker is not supported"),
            ({**self.batch_folder(Path(prog)), "on_success": "delete"}, "on_success must be \"leave\""),
            ({"hash_batch": False}, "hash_batch is only allowed"),
            (self.batch_folder(Path(prog), timeout_seconds=0), "timeout_seconds must be between"),
            (self.batch_folder(Path(prog), busy_exit_code=0), "busy_exit_code must be between"),
            (self.batch_folder(Path(prog), args=["ok", 3]), "args must be a list of strings"),
            (self.batch_folder(Path("bin/prog.sh")), "absolute, normalized path"),
            (self.batch_folder(self.root / "bin" / "missing.sh"), "missing.sh"),
        ]
        for folder, fragment in cases:
            with self.subTest(fragment=fragment), self.assertRaises(ConfigError) as cm:
                self.config(folder)
            self.assertIn(fragment, str(cm.exception))


class TestCheckProgram(BatchCase):
    def test_ok_returns_sha256(self) -> None:
        p = self.program("true")
        self.assertRegex(command.check_program(p), r"^[0-9a-f]{64}$")

    def test_refusals(self) -> None:
        p = self.program("true")
        os.symlink(p, self.root / "bin" / "link.sh")
        self.assertRaisesRegex(command.ProgramError, "symlink", command.check_program, self.root / "bin" / "link.sh")
        os.chmod(p, 0o775)
        self.assertRaisesRegex(command.ProgramError, "group- or world-writable", command.check_program, p)
        os.chmod(p, 0o644)
        self.assertRaisesRegex(command.ProgramError, "not executable", command.check_program, p)
        os.chmod(p, 0o755)
        os.chmod(self.root / "bin", 0o777)
        self.assertRaisesRegex(command.ProgramError, "directory must not be", command.check_program, p)
        os.chmod(self.root / "bin", 0o755)
        with mock.patch.object(command, "TRUSTED_OWNERS", frozenset({0})):
            self.assertRaisesRegex(command.ProgramError, "must be owned by root", command.check_program, p)


class TestBatchRuns(BatchCase):
    def test_success_empties_folder(self) -> None:
        prog = self.program('echo "path=$PATH"\nfor f in "$MONFILE_FOLDER_PATH"/*; do echo "sent $(basename "$f")"; '
                            'rm -f -- "$f"; done\ntr "\\0" "\\n" < "$MONFILE_BATCH_LIST" | sort | tr "\\n" " "; echo')
        r = self.runner(self.batch_folder(prog))
        self.write("a.cab", b"aaa")
        self.write("b.cab", b"bbbb")
        r.run_cycle(now=0)
        self.assertEqual((self.cap.events(), self.state(r)), (["BATCH_SETTLING"], SETTLING))
        r.run_cycle(now=1)
        self.assertEqual(r.batches.runs, {})
        r.run_cycle(now=2)
        self.assertIn("t", r.batches.runs)
        self.assertEqual(self.state(r), "RUNNING")
        self.finish(r, 2)

        ev = self.cap.events()
        for e in ("BATCH_READY", "COMMAND_START", "COMMAND_OUTPUT", "BATCH_DONE"):
            self.assertIn(e, ev)
        out = [line for line in self.cap.find("COMMAND_OUTPUT")]
        self.assertIn(f'line="path={command.SAFE_PATH}"', out[0])  # environment is fixed, not inherited
        self.assertTrue(any('line="sent a.cab"' in line for line in out))
        self.assertTrue(any('line="a.cab b.cab "' in line for line in out))  # MONFILE_BATCH_LIST
        self.assertIn("remaining=0", self.cap.find("BATCH_DONE")[0])
        self.assertEqual(list((self.root / "in").iterdir()), [])
        self.assertEqual(self.state(r), IDLE)
        self.assertEqual(list((self.root / "state" / "batches").iterdir()), [])  # list file removed

        manifests = list((self.root / "state" / "manifests" / "t").rglob("*.json"))
        self.assertEqual(len(manifests), 1)
        m = json.loads(manifests[0].read_text())
        self.assertEqual((m["kind"], m["status"], m["command"]["exit_code"]), ("batch", "DONE", 0))
        self.assertEqual(m["command"]["sha256"], command.check_program(prog))
        self.assertEqual([f["path"] for f in m["batch"]["files"]], ["a.cab", "b.cab"])
        self.assertEqual(len(m["batch"]["files"][0]["sha256"]), 64)

        r.run_cycle(now=100)  # empty folder stays idle and quiet
        self.assertEqual(self.cap.events().count("BATCH_SETTLING"), 1)

    def test_incomplete_holds_until_retry(self) -> None:
        r = self.runner(self.batch_folder(self.program("echo did nothing")))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        t = self.finish(r, 2)
        self.assertIn("remaining=1", self.cap.find("BATCH_INCOMPLETE")[0])
        self.assertEqual(self.state(r), HELD)
        for _ in range(3):
            t += 10
            r.run_cycle(now=t)
        self.assertEqual(self.cap.events().count("COMMAND_START"), 1)  # held: no automatic re-run

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(_retry_batch(r.cfg, r.cfg.folders[0]), 0)
        self.assertIn("RETRY_REQUESTED", self.cap.events())
        self.assertEqual(self.state(r), PENDING)
        r.run_cycle(now=t + 1)
        self.finish(r, t + 1)
        self.assertEqual(self.cap.events().count("COMMAND_START"), 2)

    def test_failure_then_new_files_settle_again(self) -> None:
        r = self.runner(self.batch_folder(self.program("echo oops >&2; exit 3")))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        t = self.finish(r, 2)
        fail = self.cap.find("COMMAND_FAIL")[0]
        self.assertIn("exit_code=3", fail)
        self.assertIn('error="exit code 3"', fail)
        self.assertTrue(any('line="oops"' in line for line in self.cap.find("COMMAND_OUTPUT")))  # stderr captured
        self.assertEqual(self.state(r), HELD)
        self.write("b.cab")
        r.run_cycle(now=t + 1)
        self.assertEqual(self.state(r), SETTLING)
        self.assertEqual(self.cap.events().count("BATCH_SETTLING"), 2)

    def test_busy_retries_later(self) -> None:
        r = self.runner(self.batch_folder(self.program("exit 75"), busy_exit_code=75))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        t = self.finish(r, 2)
        self.assertIn(f"retry_in={BUSY_RETRY_SECONDS}", self.cap.find("COMMAND_BUSY")[0])
        self.assertEqual(self.state(r), PENDING)
        r.run_cycle(now=t + 1)
        self.assertEqual(self.cap.events().count("COMMAND_START"), 1)
        r.run_cycle(now=t + BUSY_RETRY_SECONDS)
        self.assertEqual(self.cap.events().count("COMMAND_START"), 2)
        self.finish(r, t + BUSY_RETRY_SECONDS)
        self.assertNotIn("BATCH_SETTLING", self.cap.events()[1:])  # retried without a new quiet window

    def test_timeout_stops_process_group(self) -> None:
        r = self.runner(self.batch_folder(self.program("sleep 30 & wait"), timeout_seconds=5))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        pid = r.batches.runs["t"].proc.pid
        r.run_cycle(now=8)  # past the 5 s timeout: SIGTERM to the group
        self.finish(r, 8)
        self.assertIn("timeout_seconds=5", self.cap.find("COMMAND_TIMEOUT")[0])
        self.assertEqual(self.state(r), HELD)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)  # the background sleep was stopped too

    def test_program_refused(self) -> None:
        prog = self.program("true")
        r = self.runner(self.batch_folder(prog))
        os.chmod(prog, 0o777)
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        self.assertIn("world-writable", self.cap.find("COMMAND_REFUSED")[0])
        self.assertNotIn("COMMAND_START", self.cap.events())
        self.assertEqual(self.state(r), HELD)

    def test_shutdown_interrupts_and_resettles(self) -> None:
        r = self.runner(self.batch_folder(self.program("sleep 30")))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        r.batches.shutdown("service stop")
        self.assertIn('reason="service stop"', self.cap.find("COMMAND_INTERRUPTED")[0])
        self.assertEqual(self.state(r), SETTLING)
        batch = r.store.get_batch(r.store.get_batch_folder("t").batch_id)
        self.assertEqual(batch.outcome, "INTERRUPTED")

    def test_restart_after_crash_marks_interrupted(self) -> None:
        r = self.runner(self.batch_folder(self.program("sleep 30")))
        self.write("a.cab")
        for t in (0, 1, 2):
            r.run_cycle(now=t)
        r.batches.runs["t"].proc.kill()  # simulate the service dying: the run is never finished
        r.batches.runs["t"].proc.wait()
        r2 = Runner(r.cfg, r.store)
        r2.startup(now=50)
        self.assertIn('reason="monfile restarted"', self.cap.find("COMMAND_INTERRUPTED")[0])
        self.assertEqual(self.state(r2), SETTLING)

    def test_scan_mode_refuses(self) -> None:
        r = self.runner(self.batch_folder(self.program("true")), allow_commands=False)
        self.write("a.cab")
        r.run_cycle(now=0)
        self.assertIn("needs the daemon", self.cap.find("COMMAND_REFUSED")[0])

    def test_dry_run_starts_nothing(self) -> None:
        r = self.runner(self.batch_folder(self.program("rm -f -- \"$MONFILE_FOLDER_PATH\"/*")), dry_run=True)
        self.write("a.cab")
        for t in (0, 1, 2, 3):
            r.run_cycle(now=t)
        self.assertEqual(r.batches.runs, {})
        self.assertIn("would=COMMAND_START", self.cap.find("DRY_RUN")[0])
        self.assertTrue((self.root / "in" / "a.cab").exists())
        self.assertIsNone(r.store.get_batch_folder("t"))  # dry runs keep no state


class TestSchemaUpgrade(TreeCase):
    def test_v1_database_is_upgraded(self) -> None:
        db = self.root / "state" / "state.db"
        s = StateStore(db)
        s.db.execute("DROP TABLE batch_folders")
        s.db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        s.close()
        s = StateStore(db)
        self.addCleanup(s.close)
        self.assertEqual(s.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], "2")
        self.assertEqual(s.batch_folders(), [])


if __name__ == "__main__":
    unittest.main()

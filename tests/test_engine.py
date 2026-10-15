"""End-to-end cycles through the runner, with simulated time (DESIGN §4, §6, §7)."""

from __future__ import annotations

import hashlib
import json
import os
import unittest

from helpers import TreeCase

from monfile.runner import Runner
from monfile.scanner import safe_join, scan_folder
from monfile.state import ProcessLock, LockBusy


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestScanner(TreeCase):
    def test_filters_symlinks_markers(self) -> None:
        cfg = self.config({"recursive": True, "ready_marker": ".done"})
        self.write("a.txt")
        self.write("a.txt.done")
        self.write("b.part")
        self.write(".hidden")
        self.write("sub/c.txt")
        self.write(".tmpdir/d.txt")
        os.symlink(self.root / "in" / "a.txt", self.root / "in" / "link.txt")
        os.mkfifo(self.root / "in" / "fifo")
        r = scan_folder(cfg.folders[0])
        self.assertEqual([c.rel_path for c in r.candidates], ["a.txt", "sub/c.txt"])
        self.assertEqual(r.markers, {"a.txt.done"})
        self.assertEqual(r.symlinks, ["link.txt"])

    def test_not_recursive(self) -> None:
        cfg = self.config()
        self.write("sub/c.txt")
        self.assertEqual(scan_folder(cfg.folders[0]).candidates, [])

    def test_safe_join(self) -> None:
        for bad in ("", "/etc/passwd", "../x", "a/../../x", "a//b", "a\0b", "./a"):
            with self.assertRaises(ValueError, msg=bad):
                safe_join(self.root, bad)
        self.assertEqual(safe_join(self.root, "a/b"), self.root / "a" / "b")


class TestCycle(TreeCase):
    def runner(self, folder: dict | None = None, *, dry_run: bool = False) -> Runner:
        r = Runner(self.config(folder), self.store(), dry_run=dry_run)
        r.startup(now=0)
        return r

    def settle(self, r: Runner, start: float = 0) -> None:
        for t in (start, start + 1, start + 2):
            r.run_cycle(now=t)

    def test_copy_then_move_to_processed(self) -> None:
        r = self.runner({"on_success": "move", "processed_dir": str(self.root / "processed"), "manifest": True})
        self.write("report.pdf", b"data")
        r.run_cycle(now=0)
        self.assertEqual(self.cap.events(), ["FILE_DETECTED"])
        r.run_cycle(now=1)
        self.assertFalse((self.root / "out" / "report.pdf").exists())
        r.run_cycle(now=2)
        out = self.root / "out" / "report.pdf"
        self.assertEqual(out.read_bytes(), b"data")
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o640")
        self.assertTrue((self.root / "processed" / "report.pdf").exists())
        self.assertFalse((self.root / "in" / "report.pdf").exists())
        ev = self.cap.events()
        for e in ("FILE_READY", "ACTION_START", "ACTION_OK", "DISPOSITION_OK", "FILE_DONE"):
            self.assertIn(e, ev)
        self.assertIn(f"sha256={sha(b'data')}", self.cap.find("FILE_READY")[0])
        manifests = list((self.root / "state" / "manifests" / "t").rglob("*.json"))
        self.assertEqual(len(manifests), 1)
        m = json.loads(manifests[0].read_text())
        self.assertEqual(m["status"], "DONE")
        self.assertEqual(m["source"]["sha256"], sha(b"data"))
        self.assertEqual(m["actions"][0]["dest_sha256"], sha(b"data"))
        self.assertEqual(m["disposition"]["mode"], "move")
        self.assertEqual(list((self.root / "out").iterdir()), [out])  # no temp files left

    def test_change_during_settle_resets(self) -> None:
        r = self.runner()
        p = self.write("a.txt", b"one")
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        p.write_bytes(b"two!")
        r.run_cycle(now=2)
        self.assertIn("FILE_CHANGED_DURING_SETTLE", self.cap.events())
        self.assertFalse((self.root / "out" / "a.txt").exists())
        r.run_cycle(now=3)
        r.run_cycle(now=4)
        self.assertEqual((self.root / "out" / "a.txt").read_bytes(), b"two!")

    def test_hash_change_with_same_stat(self) -> None:
        r = self.runner()
        p = self.write("a.txt", b"aaaa")
        st = p.stat()
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        p.write_bytes(b"bbbb")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size and mtime: only the hash differs
        r.run_cycle(now=2)
        self.assertIn("reason=hash", " ".join(self.cap.find("FILE_CHANGED_DURING_SETTLE")))
        r.run_cycle(now=4)
        self.assertEqual((self.root / "out" / "a.txt").read_bytes(), b"bbbb")

    def test_leave_then_new_version(self) -> None:
        r = self.runner()
        p = self.write("a.txt", b"v1")
        self.settle(r)
        self.assertEqual((self.root / "out" / "a.txt").read_bytes(), b"v1")
        r.run_cycle(now=3)  # unchanged DONE file: nothing happens
        self.assertEqual(self.cap.events().count("FILE_DONE"), 1)
        p.write_bytes(b"version 2")
        self.settle(r, 10)
        self.assertIn("FILE_NEW_VERSION", self.cap.events())
        self.assertEqual(self.cap.events().count("FILE_DONE"), 2)
        suffixed = [x.name for x in (self.root / "out").iterdir() if x.name != "a.txt"]
        self.assertEqual(len(suffixed), 1)
        self.assertRegex(suffixed[0], r"^a\.\d{8}T\d{6}Z\.txt$")
        self.assertIn("COLLISION_RESOLVED", self.cap.events())

    def test_identical_existing_destination_is_success(self) -> None:
        (self.root / "out" / "a.txt").write_bytes(b"same")
        r = self.runner({"action": [{"type": "copy", "destination": str(self.root / "out"), "collision": "fail"}]})
        self.write("a.txt", b"same")
        self.settle(r)
        self.assertIn("FILE_DONE", self.cap.events())
        self.assertIn("policy=identical", " ".join(self.cap.find("COLLISION_RESOLVED")))

    def test_collision_fail_then_retry(self) -> None:
        (self.root / "out" / "a.txt").write_bytes(b"other")
        r = self.runner({"action": [{"type": "copy", "destination": str(self.root / "out"), "collision": "fail"}]})
        self.write("a.txt", b"mine")
        self.settle(r)
        self.assertIn("ACTION_FAIL", self.cap.events())
        rec = r.store.get("t", "a.txt")
        assert rec is not None
        self.assertEqual(rec.status, "FAILED")
        self.assertTrue((self.root / "in" / "a.txt").exists())
        r.run_cycle(now=5)  # FAILED is not retried automatically
        self.assertEqual(self.cap.events().count("ACTION_FAIL"), 1)
        (self.root / "out" / "a.txt").unlink()
        r.store.put(rec.with_(status="READY"))  # what `monfile retry` does
        r.run_cycle(now=6)
        self.assertEqual((self.root / "out" / "a.txt").read_bytes(), b"mine")

    def test_move_action_copy_mode(self) -> None:
        r = self.runner({"action": [{"type": "move", "destination": str(self.root / "out"), "preserve_tree": True}],
                         "recursive": True})
        self.write("sub/a.txt", b"x")
        self.settle(r)
        self.assertEqual((self.root / "out" / "sub" / "a.txt").read_bytes(), b"x")
        self.assertFalse((self.root / "in" / "sub" / "a.txt").exists())
        r.run_cycle(now=3)  # the record stays (gone); no FILE_VANISHED for a consumed file
        self.assertNotIn("FILE_VANISHED", self.cap.events())
        self.write("sub/a.txt", b"y")  # same name again: a fresh file, not a new version
        self.settle(r, 10)
        self.assertNotIn("FILE_NEW_VERSION", self.cap.events())
        self.assertEqual(self.cap.events().count("FILE_DONE"), 2)

    def test_move_action_rename_mode(self) -> None:
        r = self.runner({"action": [{"type": "move", "destination": str(self.root / "out"), "mode": "rename"}]})
        p = self.write("a.txt", b"r")
        ino = p.stat().st_ino
        self.settle(r)
        self.assertEqual((self.root / "out" / "a.txt").stat().st_ino, ino)

    def test_delete_disposition(self) -> None:
        r = self.runner({"on_success": "delete"})
        self.write("a.txt")
        self.settle(r)
        self.assertFalse((self.root / "in" / "a.txt").exists())
        self.assertTrue((self.root / "out" / "a.txt").exists())

    def test_ready_marker(self) -> None:
        r = self.runner({"ready_marker": ".done"})
        self.write("a.txt")
        self.settle(r)
        r.run_cycle(now=10)
        self.assertNotIn("FILE_READY", self.cap.events())  # settled, but no marker
        self.write("a.txt.done", b"")
        r.run_cycle(now=11)
        self.assertIn("MARKER_SEEN", self.cap.events())
        self.assertIn("FILE_DONE", self.cap.events())
        self.assertIn("MARKER_REMOVED", self.cap.events())
        self.assertFalse((self.root / "in" / "a.txt.done").exists())

    def test_orphan_marker_logged_once(self) -> None:
        r = self.runner({"ready_marker": ".done"})
        self.write("x.txt.done", b"")
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        self.assertEqual(self.cap.events().count("MARKER_ORPHAN"), 1)
        self.assertTrue((self.root / "in" / "x.txt.done").exists())

    def test_skip_empty(self) -> None:
        r = self.runner({"skip_empty": True})
        self.write("empty.txt", b"")
        self.settle(r)
        self.assertIn("FILE_SKIPPED", self.cap.events())
        self.assertFalse((self.root / "out" / "empty.txt").exists())

    def test_empty_processed_by_default(self) -> None:
        r = self.runner()
        self.write("empty.txt", b"")
        self.settle(r)
        self.assertEqual((self.root / "out" / "empty.txt").read_bytes(), b"")

    def test_verify_hash_off(self) -> None:
        r = self.runner({"verify_hash": False})
        self.write("a.txt", b"nh")
        self.settle(r)
        self.assertIn(f"sha256={sha(b'nh')}", self.cap.find("ACTION_OK")[0])
        self.assertIn("sha256=none", self.cap.find("FILE_READY")[0])

    def test_vanished_while_settling(self) -> None:
        r = self.runner()
        p = self.write("a.txt")
        r.run_cycle(now=0)
        p.unlink()
        r.run_cycle(now=1)
        self.assertIn("FILE_VANISHED", self.cap.events())
        self.assertIsNone(r.store.get("t", "a.txt"))

    def test_symlink_rejected_once(self) -> None:
        r = self.runner()
        os.symlink("/etc/passwd", self.root / "in" / "evil")
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        self.assertEqual(self.cap.events().count("SYMLINK_REJECTED"), 1)

    def test_dry_run_changes_nothing(self) -> None:
        r = self.runner({"on_success": "delete"}, dry_run=True)
        self.write("a.txt")
        self.settle(r)
        self.assertTrue((self.root / "in" / "a.txt").exists())
        self.assertEqual(list((self.root / "out").iterdir()), [])
        would = " ".join(self.cap.find("DRY_RUN"))
        self.assertIn("would=ACTION_OK", would)
        self.assertIn("would=DISPOSITION_OK", would)

    def test_recovery_of_in_progress(self) -> None:
        r = self.runner()
        self.write("a.txt")
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        rec = r.store.get("t", "a.txt")
        assert rec is not None
        r.store.put(rec.with_(status="IN_PROGRESS", hash_end=rec.hash_start))  # simulate a crash mid-chain
        self.assertEqual(r.store.recover(), 1)
        r.run_cycle(now=2)
        self.assertTrue((self.root / "out" / "a.txt").exists())

    def test_temp_cleanup(self) -> None:
        stale = self.root / "out" / ".monfile-tmp-deadbeef"
        stale.write_bytes(b"partial")
        os.utime(stale, (1, 1))
        fresh = self.root / "out" / ".monfile-tmp-cafe"
        fresh.write_bytes(b"partial")
        r = Runner(self.config(), self.store())
        r.startup()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertIn("TEMP_CLEANUP", self.cap.events())


class TestLock(TreeCase):
    def test_second_lock_is_busy(self) -> None:
        lock = ProcessLock(self.root / "state" / "x.lock")
        self.addCleanup(lock.release)
        with self.assertRaises(LockBusy):
            ProcessLock(self.root / "state" / "x.lock")


if __name__ == "__main__":
    unittest.main()


class TestDelivery(TreeCase):
    def test_integrity_mismatch_leaves_nothing(self) -> None:
        from monfile.actions import ActionContext
        from monfile.actions.copy import CopyAction

        cfg = self.config()
        p = self.write("a.txt", b"real")
        st = p.stat()
        ctx = ActionContext(folder=cfg.folders[0], source=p, rel_path="a.txt", source_sha256=sha(b"other"),
                            source_stat=(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns), dry_run=False)
        result = CopyAction({"destination": str(self.root / "out")}).run(ctx)
        self.assertFalse(result.ok)
        self.assertIn("INTEGRITY_MISMATCH", self.cap.events())
        self.assertIn("stage=stream", self.cap.find("INTEGRITY_MISMATCH")[0])
        self.assertEqual(list((self.root / "out").iterdir()), [])

    def test_source_changed_since_settle(self) -> None:
        from monfile.actions import ActionContext
        from monfile.actions.copy import CopyAction

        cfg = self.config()
        p = self.write("a.txt", b"real")
        ctx = ActionContext(folder=cfg.folders[0], source=p, rel_path="a.txt", source_sha256=None,
                            source_stat=(0, 0, 0, 0), dry_run=False)
        result = CopyAction({"destination": str(self.root / "out")}).run(ctx)
        self.assertFalse(result.ok)
        self.assertIn("changed after it settled", result.error or "")


class TestCrossMountRename(TreeCase):
    """systemd ReadWritePaths= makes each path its own mount: rename() gives EXDEV despite equal st_dev."""

    def _exdev(self):
        import errno
        from unittest import mock

        real = os.rename

        def fake(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            if str(src).startswith(".monfile-tmp-"):
                return real(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            raise OSError(errno.EXDEV, "Invalid cross-device link", src)

        return mock.patch("monfile.actions.deliver.os.rename", side_effect=fake)

    def test_processed_disposition_falls_back_to_copy(self) -> None:
        r = Runner(self.config({"on_success": "move", "processed_dir": str(self.root / "processed")}), self.store())
        r.startup(now=0)
        self.write("a.txt", b"x")
        with self._exdev():
            for t in (0, 1, 2):
                r.run_cycle(now=t)
        self.assertIn("DISPOSITION_OK", self.cap.events())
        self.assertEqual((self.root / "processed" / "a.txt").read_bytes(), b"x")
        self.assertFalse((self.root / "in" / "a.txt").exists())

    def test_move_action_rename_mode_reports_mounts(self) -> None:
        r = Runner(self.config({"action": [{"type": "move", "destination": str(self.root / "out"),
                                            "mode": "rename"}]}), self.store())
        r.startup(now=0)
        self.write("a.txt", b"x")
        with self._exdev():
            for t in (0, 1, 2):
                r.run_cycle(now=t)
        self.assertIn("ReadWritePaths", " ".join(self.cap.find("ACTION_FAIL")))
        self.assertTrue((self.root / "in" / "a.txt").exists())

    def test_retry_after_disposition_failure_skips_done_copy(self) -> None:
        cfg = self.config({"on_success": "move", "processed_dir": str(self.root / "processed")})
        r = Runner(cfg, self.store())
        r.startup(now=0)
        self.write("a.txt", b"x")
        (self.root / "processed").chmod(0o500)  # disposition fails; the copy has already succeeded
        try:
            for t in (0, 1, 2):
                r.run_cycle(now=t)
        finally:
            (self.root / "processed").chmod(0o700)
        self.assertIn("DISPOSITION_FAIL", self.cap.events())
        rec = r.store.get("t", "a.txt")
        assert rec is not None and rec.status == "FAILED"
        r.store.put(rec.with_(status="READY"))  # monfile retry
        r.run_cycle(now=3)
        self.assertIn("ACTION_SKIPPED_DONE", self.cap.events())
        self.assertEqual(sorted(p.name for p in (self.root / "out").iterdir()), ["a.txt"])  # no second copy
        self.assertTrue((self.root / "processed" / "a.txt").exists())


class TestNoticesOnce(TreeCase):
    def test_nonregular_logged_once(self) -> None:
        r = Runner(self.config(), self.store())
        os.mkfifo(self.root / "in" / "pipe")
        (self.root / "in" / "subdir").mkdir()
        r.run_cycle(now=0)
        r.run_cycle(now=1)
        lines = self.cap.find("FILE_IGNORED_NONREGULAR")
        self.assertEqual(len(lines), 2)
        self.assertIn("type=fifo", " ".join(lines))
        self.assertIn("type=dir", " ".join(lines))

    def test_hardlink_duplicate_logged_once_and_both_processed(self) -> None:
        r = Runner(self.config(), self.store())
        a = self.write("a.txt", b"shared")
        os.link(a, self.root / "in" / "b.txt")
        for t in (0, 1, 2, 3):
            r.run_cycle(now=t)
        lines = self.cap.find("FILE_HARDLINK_DUPLICATE")
        self.assertEqual(len(lines), 1)
        self.assertIn("b.txt", lines[0])
        self.assertIn('same_as="', lines[0])
        self.assertEqual(sorted(p.name for p in (self.root / "out").iterdir()), ["a.txt", "b.txt"])

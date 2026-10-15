"""Packaging checks (ADR-0008, ADR-0010): script syntax and the tarball's MANIFEST.sha256."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = [
    ROOT / "packaging" / "install.sh",
    ROOT / "packaging" / "uninstall.sh",
    ROOT / "packaging" / "build-tarball.sh",
    ROOT / "packaging" / "lib" / "common.sh",
]


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class TestScriptSyntax(unittest.TestCase):
    def test_bash_syntax(self) -> None:
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                subprocess.run(["bash", "-n", str(script)], check=True)


@unittest.skipUnless(all(shutil.which(t) for t in ("bash", "tar", "sha256sum")), "build tools not available")
class TestTarballManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        subprocess.run(
            [str(ROOT / "packaging" / "build-tarball.sh")],
            check=True, capture_output=True, env={**os.environ, "DIST": str(tmp / "dist")},
        )
        (tarball,) = (tmp / "dist").glob("monfile-*.tar.gz")
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp / "x", filter="data")
        (cls.tree,) = (tmp / "x").iterdir()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def manifest_paths(self) -> set[str]:
        lines = (self.tree / "MANIFEST.sha256").read_text().splitlines()
        return {line.split("  ", 1)[1] for line in lines}

    def test_manifest_verifies(self) -> None:
        subprocess.run(
            ["sha256sum", "--quiet", "--strict", "-c", "MANIFEST.sha256"],
            cwd=self.tree, check=True,
        )

    def test_manifest_covers_every_file(self) -> None:
        files = {
            p.relative_to(self.tree).as_posix()
            for p in self.tree.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256"
        }
        self.assertEqual(files, self.manifest_paths())

    def test_installer_inputs_present(self) -> None:
        needed = {
            "packaging/install.sh", "packaging/uninstall.sh", "packaging/lib/common.sh",
            "packaging/systemd/monfile.service", "packaging/systemd/monfile-scan.service",
            "packaging/systemd/monfile-scan.timer", "packaging/tmpfiles.d/monfile.conf",
            "src/monfile/__init__.py", "src/monfile/cli.py", "config/monfile.example.toml", "HOWTO.md",
        }
        self.assertLessEqual(needed, self.manifest_paths())

    def test_no_symlinks_or_bytecode(self) -> None:
        for p in self.tree.rglob("*"):
            with self.subTest(path=str(p.relative_to(self.tree))):
                self.assertFalse(p.is_symlink())
                self.assertNotEqual(p.suffix, ".pyc")


if __name__ == "__main__":
    unittest.main()

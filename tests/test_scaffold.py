"""Guard-rail tests that are valid even before implementation."""

from __future__ import annotations

import ast
import contextlib
import io
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "monfile"


class TestCli(unittest.TestCase):
    def test_help_parses(self) -> None:
        from monfile.cli import build_parser

        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            build_parser().parse_args(["--help"])
        self.assertEqual(cm.exception.code, 0)


class TestExampleConfig(unittest.TestCase):
    def test_example_config_is_valid_toml(self) -> None:
        with open(ROOT / "config" / "monfile.example.toml", "rb") as fh:
            data = tomllib.load(fh)
        self.assertIn("global", data)
        self.assertGreaterEqual(len(data["folder"]), 1)


class TestStdlibOnly(unittest.TestCase):
    """SR-02: no third-party runtime imports."""

    def test_imports_are_stdlib_or_local(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"monfile", "__future__"}
        for py in SRC.rglob("*.py"):
            tree = ast.parse(py.read_text(), filename=str(py))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    with self.subTest(file=py.name, module=name):
                        self.assertIn(name.split(".")[0], allowed)


class TestAuditCatalogSync(unittest.TestCase):
    """AGENTS.md rule 6: EventID enum and docs/audit/AUDIT_EVENTS.md must match."""

    def test_event_ids_match_catalog(self) -> None:
        from monfile.audit import EventID

        doc = (ROOT / "docs" / "audit" / "AUDIT_EVENTS.md").read_text()
        documented = set(re.findall(r"^\| `([A-Z_]+)` \|", doc, flags=re.M))
        self.assertEqual({e.value for e in EventID}, documented)


if __name__ == "__main__":
    unittest.main()

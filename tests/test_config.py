"""Config validation rules (DESIGN §5, SR-06)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any

from helpers import TreeCase

from monfile.config import Config, ConfigError, ConfigPermsError, GlobalConfig, load_config, parse_config


class TestConfigRules(TreeCase):
    def base(self, **folder: Any) -> dict[str, Any]:
        f: dict[str, Any] = {"id": "a", "path": str(self.root / "in"), "settle_seconds": 60,
                             "action": [{"type": "copy", "destination": str(self.root / "out")}]}
        f.update(folder)
        return {"global": {}, "folder": [f]}

    def assertInvalid(self, data: dict[str, Any], fragment: str) -> None:
        with self.assertRaises(ConfigError) as cm:
            parse_config(data)
        self.assertIn(fragment, str(cm.exception))

    def test_valid_minimal(self) -> None:
        g, folders = parse_config(self.base())
        self.assertEqual(folders[0].id, "a")
        self.assertEqual(folders[0].exclude[-1], "*.filepart")

    def test_unknown_key(self) -> None:
        self.assertInvalid(self.base(setle_seconds=60), "unknown key 'setle_seconds'")

    def test_unknown_action_param(self) -> None:
        data = self.base(action=[{"type": "copy", "destination": str(self.root / "out"), "overwrite": True}])
        self.assertInvalid(data, "unknown key 'overwrite'")

    def test_disabled_folder_skips_path_checks(self) -> None:
        data = self.base(enabled=False, path=str(self.root / "missing"))
        data["folder"][0]["action"][0]["destination"] = str(self.root / "missing-out")
        _, folders = parse_config(data)
        cfg = Config.build(GlobalConfig(), folders, Path("x.toml"), "0" * 64)
        self.assertEqual((cfg.folders, [f.id for f in cfg.disabled]), ((), ["a"]))
        self.assertInvalid(self.base(enabled=False, setle_seconds=60), "unknown key 'setle_seconds'")
        self.assertInvalid(self.base(enabled="no"), "enabled must be bool")

    def test_disabled_folder_may_overlap_but_not_reuse_id(self) -> None:
        data = self.base()
        other = dict(data["folder"][0], id="b", enabled=False)  # same watch path as "a"
        data["folder"].append(other)
        parse_config(data)
        data["folder"][1]["id"] = "a"
        self.assertInvalid(data, "duplicate id")

    def test_duplicate_id(self) -> None:
        data = self.base()
        other = dict(data["folder"][0], path=str(self.root / "processed"))
        other["action"] = [{"type": "copy", "destination": str(self.root / "state")}]
        data["folder"].append(other)
        self.assertInvalid(data, "duplicate id")

    def test_settle_minimum(self) -> None:
        self.assertInvalid(self.base(settle_seconds=59), "settle_seconds (59) must be >= 2 x")

    def test_missing_path(self) -> None:
        self.assertInvalid(self.base(path=str(self.root / "nope")), "No such file")

    def test_symlink_path(self) -> None:
        os.symlink(self.root / "in", self.root / "link")
        self.assertInvalid(self.base(path=str(self.root / "link")), "must not contain symlinks")

    def test_output_inside_watch(self) -> None:
        (self.root / "in" / "sub").mkdir()
        data = self.base(action=[{"type": "copy", "destination": str(self.root / "in" / "sub")}])
        self.assertInvalid(data, "inside the watch path")

    def test_processed_dir_required(self) -> None:
        self.assertInvalid(self.base(on_success="move"), "processed_dir is required")

    def test_move_must_be_last_and_leave(self) -> None:
        mv = {"type": "move", "destination": str(self.root / "processed")}
        cp = {"type": "copy", "destination": str(self.root / "out")}
        self.assertInvalid(self.base(action=[mv, cp]), "must be last")
        self.assertInvalid(self.base(action=[mv], on_success="delete"), "must be \"leave\"")

    def test_ready_marker_format(self) -> None:
        self.assertInvalid(self.base(ready_marker="done"), "ready_marker must match")
        _, folders = parse_config(self.base(ready_marker=".done"))
        self.assertEqual(folders[0].ready_marker, ".done")

    def test_bool_is_not_int(self) -> None:
        self.assertInvalid(self.base(settle_seconds=True), "settle_seconds must be int")


class TestConfigFile(TreeCase):
    def test_non_root_owner_refused(self) -> None:
        p = self.root / "monfile.toml"
        p.write_text('[global]\n[[folder]]\nid="a"\n')
        if os.geteuid() == 0:
            self.skipTest("running as root")
        with self.assertRaises(ConfigPermsError):
            load_config(p)

    def test_example_config_schema(self) -> None:
        example = Path(__file__).resolve().parent.parent / "config" / "monfile.example.toml"
        cfg = load_config(example, check_perms=False, check_paths=False)
        self.assertEqual([f.id for f in cfg.disabled], ["inbound-a", "bulk-drop"])

    def test_example_config_folders_ship_disabled(self) -> None:
        """A copied example must load (its /data paths don't exist) and watch nothing until edited."""
        example = Path(__file__).resolve().parent.parent / "config" / "monfile.example.toml"
        cfg = load_config(example, check_perms=False)
        self.assertEqual(cfg.folders, ())


if __name__ == "__main__":
    unittest.main()

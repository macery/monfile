"""Configuration loading and validation (DESIGN §5, ADR-0004)."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Disposition = Literal["leave", "move", "delete"]
Collision = Literal["suffix", "skip", "fail"]

FOLDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
READY_MARKER_RE = re.compile(r"^\.[A-Za-z0-9_-]{1,32}$")
DEFAULT_EXCLUDE = (".*", "*.part", "*.tmp", "~$*", "*.crdownload", "*.filepart")
LOG_LEVELS = ("debug", "info", "warning", "error")
SYSLOG_FACILITIES = ("daemon", "user", *(f"local{i}" for i in range(8)))


class ConfigError(Exception):
    """Invalid config contents or insecure config permissions."""


class ConfigPermsError(ConfigError):
    """The config file's ownership or mode is insecure (SR-06)."""

    def __init__(self, message: str, owner: int, mode: int) -> None:
        super().__init__(message)
        self.owner, self.mode = owner, mode


@dataclass(frozen=True)
class ActionConfig:
    type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FolderConfig:
    id: str
    path: Path
    recursive: bool = False
    settle_seconds: int = 300
    verify_hash: bool = True
    skip_empty: bool = False
    manifest: bool = False
    include: tuple[str, ...] = ("*",)
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE
    on_success: Disposition = "leave"
    processed_dir: Path | None = None
    ready_marker: str | None = None  # e.g. ".done"; None = settle-only (DESIGN §4.4)
    actions: tuple[ActionConfig, ...] = ()

    @property
    def consumes_source(self) -> bool:
        """True if the source is gone after success (move action, or move/delete disposition)."""
        return self.on_success != "leave" or any(a.type == "move" for a in self.actions)

    def output_roots(self) -> list[Path]:
        """Every directory monfile writes into for this folder."""
        roots = [Path(a.params["destination"]) for a in self.actions]
        if self.processed_dir is not None:
            roots.append(self.processed_dir)
        return roots


@dataclass(frozen=True)
class GlobalConfig:
    state_dir: Path = Path("/var/lib/monfile")
    manifest_dir: Path = Path("/var/lib/monfile/manifests")
    scan_interval_seconds: int = 30
    log_level: str = "info"
    syslog_facility: str = "daemon"
    hash_chunk_bytes: int = 1024 * 1024
    max_files_per_cycle: int = 1000


@dataclass(frozen=True)
class Config:
    global_: GlobalConfig
    folders: tuple[FolderConfig, ...]
    source_path: Path
    sha256: str  # hash of the raw config bytes, logged in CONFIG_LOADED


def check_permissions(path: Path) -> None:
    """Raise ConfigError unless the file is root-owned and not group/world-writable (SR-06)."""
    st = os.lstat(path)
    _check_mode(path, st)


def _check_mode(path: Path, st: os.stat_result) -> None:
    mode = stat.S_IMODE(st.st_mode)
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError(f"{path}: not a regular file")
    if st.st_uid != 0:
        raise ConfigPermsError(f"{path}: must be owned by root (owner uid {st.st_uid}, mode {mode:04o})", st.st_uid, mode)
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ConfigPermsError(f"{path}: must not be group- or world-writable (mode {mode:04o})", st.st_uid, mode)


def load_config(path: Path, *, check_perms: bool = True, check_paths: bool = True) -> Config:
    """Read with tomllib, check permissions, and validate per the DESIGN §5 rules.

    Unknown keys are errors. check_perms/check_paths are disabled only by
    `validate-config --schema-only` and by tests; the service always checks both.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise ConfigError(f"{path}: cannot open: {exc.strerror}") from None
    try:
        if check_perms:
            _check_mode(path, os.fstat(fd))  # fstat on the open fd: no check/use race
        with os.fdopen(fd, "rb", closefd=False) as fh:
            raw = fh.read()
    finally:
        os.close(fd)
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from None
    cfg = parse_config(data, check_paths=check_paths)
    return Config(cfg[0], cfg[1], path, hashlib.sha256(raw).hexdigest())


def parse_config(data: dict[str, Any], *, check_paths: bool = True) -> tuple[GlobalConfig, tuple[FolderConfig, ...]]:
    errors: list[str] = []
    _unknown(data, {"global", "folder"}, "top level", errors)
    g = _parse_global(_table(data.get("global", {}), "[global]", errors), errors)
    raw_folders = data.get("folder", [])
    if not isinstance(raw_folders, list) or not raw_folders:
        errors.append("at least one [[folder]] is required")
        raw_folders = []
    folders = [f for i, raw in enumerate(raw_folders) if (f := _parse_folder(raw, i, g, errors, check_paths))]
    _cross_checks(folders, errors)
    if errors:
        raise ConfigError("\n".join(errors))
    return g, tuple(folders)


# --- helpers -------------------------------------------------------------------------


def _table(value: Any, where: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{where}: must be a table")
        return {}
    return value


def _unknown(table: dict[str, Any], allowed: set[str], where: str, errors: list[str]) -> None:
    for key in sorted(set(table) - allowed):
        errors.append(f"{where}: unknown key {key!r}")


def _get(table: dict[str, Any], key: str, kind: type, default: Any, where: str, errors: list[str]) -> Any:
    value = table.get(key, default)
    ok = isinstance(value, kind) and not (kind is int and isinstance(value, bool))
    if not ok:
        errors.append(f"{where}: {key} must be {kind.__name__}, got {value!r}")
        return default
    return value


def _str_list(table: dict[str, Any], key: str, default: tuple[str, ...], where: str, errors: list[str]) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        errors.append(f"{where}: {key} must be a list of non-empty strings")
        return default
    return tuple(value)


def _abs_path(value: Any, key: str, where: str, errors: list[str], *, check: bool, must_be_dir: bool = True) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{where}: {key} must be a non-empty string")
        return None
    if not value.startswith("/") or os.path.normpath(value) != value or "\0" in value:
        errors.append(f"{where}: {key} must be an absolute, normalized path: {value!r}")
        return None
    path = Path(value)
    if check:
        if os.path.realpath(value) != value:
            errors.append(f"{where}: {key} must not contain symlinks: {value}")
        elif must_be_dir:
            try:
                st = os.lstat(value)
            except OSError as exc:
                errors.append(f"{where}: {key} {value}: {exc.strerror}")
            else:
                if not stat.S_ISDIR(st.st_mode):
                    errors.append(f"{where}: {key} is not a directory: {value}")
    return path


def _parse_global(t: dict[str, Any], errors: list[str]) -> GlobalConfig:
    where = "[global]"
    d = GlobalConfig()
    _unknown(t, set(GlobalConfig.__dataclass_fields__), where, errors)
    state_dir = _abs_path(t.get("state_dir", str(d.state_dir)), "state_dir", where, errors, check=False) or d.state_dir
    manifest_dir = _abs_path(t.get("manifest_dir", str(d.manifest_dir)), "manifest_dir", where, errors, check=False) or d.manifest_dir
    g = GlobalConfig(
        state_dir=state_dir,
        manifest_dir=manifest_dir,
        scan_interval_seconds=_get(t, "scan_interval_seconds", int, d.scan_interval_seconds, where, errors),
        log_level=_get(t, "log_level", str, d.log_level, where, errors),
        syslog_facility=_get(t, "syslog_facility", str, d.syslog_facility, where, errors),
        hash_chunk_bytes=_get(t, "hash_chunk_bytes", int, d.hash_chunk_bytes, where, errors),
        max_files_per_cycle=_get(t, "max_files_per_cycle", int, d.max_files_per_cycle, where, errors),
    )
    if g.scan_interval_seconds < 1:
        errors.append(f"{where}: scan_interval_seconds must be >= 1")
    if g.log_level not in LOG_LEVELS:
        errors.append(f"{where}: log_level must be one of {', '.join(LOG_LEVELS)}")
    if g.syslog_facility not in SYSLOG_FACILITIES:
        errors.append(f"{where}: syslog_facility must be one of {', '.join(SYSLOG_FACILITIES)}")
    if not 4096 <= g.hash_chunk_bytes <= 64 * 1024 * 1024:
        errors.append(f"{where}: hash_chunk_bytes must be between 4096 and 67108864")
    if g.max_files_per_cycle < 1:
        errors.append(f"{where}: max_files_per_cycle must be >= 1")
    return g


_FOLDER_KEYS = set(FolderConfig.__dataclass_fields__) - {"actions"} | {"action"}


def _parse_folder(raw: Any, index: int, g: GlobalConfig, errors: list[str], check: bool) -> FolderConfig | None:
    from monfile.actions import get_action  # late import: actions imports this module

    where = f"[[folder]] #{index + 1}"
    t = _table(raw, where, errors)
    if not t:
        return None
    fid = t.get("id")
    if not isinstance(fid, str) or not FOLDER_ID_RE.match(fid):
        errors.append(f"{where}: id must match {FOLDER_ID_RE.pattern}, got {fid!r}")
        fid = f"#{index + 1}"
    else:
        where = f"[[folder]] {fid}"
    _unknown(t, _FOLDER_KEYS, where, errors)

    path = _abs_path(t.get("path"), "path", where, errors, check=check)
    d = FolderConfig(id=fid, path=Path("/"))
    settle = _get(t, "settle_seconds", int, d.settle_seconds, where, errors)
    if settle < 2 * g.scan_interval_seconds:
        errors.append(f"{where}: settle_seconds ({settle}) must be >= 2 x scan_interval_seconds ({g.scan_interval_seconds})")
    on_success = _get(t, "on_success", str, d.on_success, where, errors)
    if on_success not in ("leave", "move", "delete"):
        errors.append(f"{where}: on_success must be leave, move, or delete")
        on_success = "leave"
    processed_dir = None
    if "processed_dir" in t:
        processed_dir = _abs_path(t["processed_dir"], "processed_dir", where, errors, check=check)
    if on_success == "move" and "processed_dir" not in t:
        errors.append(f"{where}: processed_dir is required when on_success = \"move\"")
    elif on_success != "move" and "processed_dir" in t:
        errors.append(f"{where}: processed_dir is only allowed when on_success = \"move\"")
    marker = t.get("ready_marker")
    if marker is not None and (not isinstance(marker, str) or not READY_MARKER_RE.match(marker)):
        errors.append(f"{where}: ready_marker must match {READY_MARKER_RE.pattern}, e.g. \".done\"")
        marker = None

    actions: list[ActionConfig] = []
    raw_actions = t.get("action", [])
    if not isinstance(raw_actions, list) or not raw_actions:
        errors.append(f"{where}: at least one [[folder.action]] is required")
        raw_actions = []
    for ai, raw_action in enumerate(raw_actions):
        aw = f"{where} action #{ai + 1}"
        at = _table(raw_action, aw, errors)
        if not at:
            continue
        atype = at.get("type")
        params = {k: v for k, v in at.items() if k != "type"}
        try:
            cls = get_action(str(atype))
            cls(params)  # validates params
        except KeyError:
            errors.append(f"{aw}: unknown action type {atype!r}")
            continue
        except ConfigError as exc:
            errors.append(f"{aw}: {exc}")
            continue
        _abs_path(params["destination"], "destination", aw, errors, check=check)
        if cls.consumes_source and ai != len(raw_actions) - 1:
            errors.append(f"{aw}: a {atype} action must be last in the chain")
        if cls.consumes_source and on_success != "leave":
            errors.append(f"{where}: on_success must be \"leave\" when the chain ends with a {atype} action")
        actions.append(ActionConfig(str(atype), params))

    if path is None:
        return None
    return FolderConfig(
        id=fid,
        path=path,
        recursive=_get(t, "recursive", bool, d.recursive, where, errors),
        settle_seconds=settle,
        verify_hash=_get(t, "verify_hash", bool, d.verify_hash, where, errors),
        skip_empty=_get(t, "skip_empty", bool, d.skip_empty, where, errors),
        manifest=_get(t, "manifest", bool, d.manifest, where, errors),
        include=_str_list(t, "include", d.include, where, errors),
        exclude=_str_list(t, "exclude", d.exclude, where, errors),
        on_success=on_success,
        processed_dir=processed_dir,
        ready_marker=marker,
        actions=tuple(actions),
    )


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _cross_checks(folders: list[FolderConfig], errors: list[str]) -> None:
    seen: set[str] = set()
    for f in folders:
        if f.id in seen:
            errors.append(f"[[folder]] {f.id}: duplicate id")
        seen.add(f.id)
    for i, a in enumerate(folders):
        for b in folders[i + 1:]:
            if _inside(a.path, b.path) or _inside(b.path, a.path):
                errors.append(f"[[folder]] {a.id} and {b.id}: watch paths overlap ({a.path}, {b.path})")
    for f in folders:
        for out in f.output_roots():
            for w in folders:
                if _inside(out, w.path):
                    errors.append(f"[[folder]] {f.id}: output {out} is inside the watch path of {w.id} ({w.path})")
                elif _inside(w.path, out):
                    errors.append(f"[[folder]] {f.id}: output {out} contains the watch path of {w.id} ({w.path})")


def access_problems(cfg: Config) -> list[str]:
    """Check that the current user can use every configured directory (validate-config)."""
    problems: list[str] = []

    def need(path: Path, mode: int, why: str) -> None:
        if not os.access(path, mode):
            perm = "write" if mode & os.W_OK else "read"
            problems.append(f"{path}: no {perm} access for this user ({why})")

    for f in cfg.folders:
        need(f.path, os.R_OK | os.X_OK, f"watch folder {f.id}")
        if f.consumes_source or f.ready_marker:
            need(f.path, os.W_OK | os.X_OK, f"{f.id} removes files from its watch folder")
        for out in f.output_roots():
            need(out, os.W_OK | os.X_OK, f"output of {f.id}")
    return problems

"""Command-line entry point (DESIGN §11)."""

from __future__ import annotations

import argparse
import os
import pwd
import signal
import sys
import threading
import time
from pathlib import Path

from monfile import __version__, audit
from monfile.audit import EventID
from monfile.config import Config, ConfigError, ConfigPermsError, access_problems, load_config
from monfile.state import LOCK_PATH, LockBusy, ProcessLock, StateStore

DEFAULT_CONFIG = Path("/etc/monfile/monfile.toml")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2  # the units set RestartPreventExitStatus=2, so a bad config doesn't restart-loop


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monfile",
        description="Watch folders and run workflows on files once they settle.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-c", "--config", type=Path, default=DEFAULT_CONFIG,
        help=f"config file (default: {DEFAULT_CONFIG})",
    )
    # -c is also accepted after the subcommand ("monfile validate-config -c FILE").
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", type=Path, default=argparse.SUPPRESS, help="config file")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help, parents=[common])

    run = add("run", help="run as a daemon, scanning every scan_interval_seconds")
    run.add_argument("--dry-run", action="store_true", help="log intended actions without performing them")

    scan = add("scan", help="run scan cycle(s) and exit (for systemd timer/cron)")
    scan.add_argument("--once", action="store_true", required=True, help="run exactly one cycle")
    scan.add_argument("--dry-run", action="store_true", help="log intended actions without performing them")

    vc = add("validate-config", help="validate config contents and permissions, then exit")
    vc.add_argument("--schema-only", action="store_true",
                    help="check syntax and rules only; skip ownership, path, and access checks (for review off-host)")

    st = add("status", help="show tracked files and their states")
    st.add_argument("--all", action="store_true", help="include DONE and SKIPPED files")

    retry = add("retry", help="return a FAILED file to READY")
    retry.add_argument("folder_id")
    retry.add_argument("rel_path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-config":
        return cmd_validate(args.config, schema_only=args.schema_only)
    cfg = _load_or_log(args.config)
    if cfg is None:
        return EXIT_CONFIG
    if args.command in ("run", "scan"):
        return cmd_run(cfg, mode=args.command, dry_run=args.dry_run)
    if args.command == "status":
        return cmd_status(cfg, show_all=args.all)
    return cmd_retry(cfg, args.folder_id, args.rel_path)


def _load_or_log(path: Path) -> Config | None:
    try:
        cfg = load_config(path)
    except ConfigPermsError as exc:
        audit.setup_logging("daemon", "info")
        audit.emit(EventID.CONFIG_PERMS_INSECURE, config=path, owner=exc.owner, mode=f"{exc.mode:04o}")
        return None
    except ConfigError as exc:
        audit.setup_logging("daemon", "info")
        for line in str(exc).splitlines():
            audit.emit(EventID.CONFIG_INVALID, config=path, error=line)
        return None
    audit.setup_logging(cfg.global_.syslog_facility, cfg.global_.log_level)
    return cfg


# --- validate-config -------------------------------------------------------------------


def cmd_validate(path: Path, *, schema_only: bool) -> int:
    try:
        cfg = load_config(path, check_perms=not schema_only, check_paths=not schema_only)
    except ConfigError as exc:
        print(f"monfile: config is INVALID: {path}", file=sys.stderr)
        for line in str(exc).splitlines():
            print(f"  - {line}", file=sys.stderr)
        return EXIT_CONFIG
    problems = [] if schema_only else access_problems(cfg)
    user = pwd.getpwuid(os.geteuid()).pw_name
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    if problems:
        print(f"monfile: config is valid, but user {user!r} lacks access (see above). "
              "Run this as the monfile user to check the service's view.", file=sys.stderr)
        return EXIT_ERROR
    what = "schema" if schema_only else f"config, permissions, and access for user {user!r}"
    print(f"monfile: OK: {path} ({what}); sha256={cfg.sha256}")
    for f in cfg.folders:
        chain = " -> ".join(f"{a.type}:{a.params['destination']}" for a in f.actions)
        extra = f", marker {f.ready_marker}" if f.ready_marker else ""
        print(f"  {f.id}: {f.path} settle={f.settle_seconds}s{extra}; {chain}; on_success={f.on_success}")
    for f in cfg.disabled:
        print(f"  {f.id}: DISABLED (enabled = false; paths not checked)")
    if not cfg.folders:
        print("monfile: note: no folder is enabled, so the service will run but watch nothing")
    return EXIT_OK


# --- run / scan --once -----------------------------------------------------------------


def _fips() -> bool:
    try:
        return Path("/proc/sys/crypto/fips_enabled").read_text().strip() == "1"
    except OSError:
        return False


def cmd_run(cfg: Config, *, mode: str, dry_run: bool) -> int:
    from monfile.runner import Runner

    g = cfg.global_
    try:
        lock = ProcessLock(LOCK_PATH)
    except LockBusy:
        audit.emit(EventID.LOCK_BUSY, lock=LOCK_PATH)
        return EXIT_OK if mode == "scan" else EXIT_ERROR
    except OSError as exc:
        print(f"monfile: cannot open lock {LOCK_PATH}: {exc.strerror}", file=sys.stderr)
        return EXIT_ERROR

    stop = threading.Event()
    reason = {"value": "once-complete" if mode == "scan" else "signal"}

    def on_term(signum: int, _frame: object) -> None:
        reason["value"] = f"signal {signal.Signals(signum).name}"
        stop.set()  # finish the current file, then exit

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)  # config reload is restart-only (§13 Q10)

    audit.emit(EventID.SERVICE_START, version=__version__, mode=mode, pid=os.getpid(),
               user=pwd.getpwuid(os.geteuid()).pw_name, python=sys.executable, fips=_fips(), dryrun=dry_run)
    audit.emit(EventID.CONFIG_LOADED, config=cfg.source_path, sha256=cfg.sha256, folders=len(cfg.folders),
               disabled=len(cfg.disabled))
    for f in cfg.disabled:
        audit.emit(EventID.FOLDER_DISABLED, folder=f.id, path=f.path)
    try:
        g.state_dir.mkdir(mode=0o750, exist_ok=True)
        store = StateStore(g.state_dir / "state.db")
    except OSError as exc:
        print(f"monfile: cannot open state database in {g.state_dir}: {exc.strerror}", file=sys.stderr)
        lock.release()
        return EXIT_ERROR
    try:
        runner = Runner(cfg, store, dry_run=dry_run, stop=stop)
        runner.startup()
        if mode == "scan":
            runner.run_cycle()
        else:
            runner.run_forever()
    finally:
        store.close()
        audit.emit(EventID.SERVICE_STOP, reason=reason["value"])
        lock.release()
    return EXIT_OK


# --- status / retry --------------------------------------------------------------------


def _open_store(cfg: Config, *, readonly: bool) -> StateStore | None:
    db = cfg.global_.state_dir / "state.db"
    try:
        if not db.exists():
            print(f"monfile: no state yet ({db} does not exist). Has the service run?", file=sys.stderr)
            return None
        return StateStore(db, readonly=readonly)
    except (OSError, Exception) as exc:  # sqlite3 errors aren't OSError
        print(f"monfile: cannot open {db}: {exc}. Run this as the monfile user or root.", file=sys.stderr)
        return None


def cmd_status(cfg: Config, *, show_all: bool) -> int:
    store = _open_store(cfg, readonly=True)
    if store is None:
        return EXIT_ERROR
    try:
        records = store.all_records()
    finally:
        store.close()
    counts: dict[str, int] = {}
    rows = []
    for r in records:
        counts[r.status] = counts.get(r.status, 0) + 1
        if show_all or r.status not in ("DONE", "SKIPPED"):
            rows.append(r)
    print("summary: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no files tracked"))
    if not rows:
        return EXIT_OK
    print(f"{'FOLDER':<16} {'STATUS':<11} {'SIZE':>12}  {'UPDATED (UTC)':<20} PATH")
    for r in rows:
        updated = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r.updated_at))
        name = audit.escape(r.rel_path) + (" (gone)" if r.gone else "")
        print(f"{r.folder_id:<16} {r.status:<11} {r.size:>12}  {updated:<20} {name}")
        if r.status == "FAILED" and r.last_error:
            print(f"{'':<16} error: {audit.escape(r.last_error)}")
    return EXIT_OK


def cmd_retry(cfg: Config, folder_id: str, rel_path: str) -> int:
    if folder_id in {f.id for f in cfg.disabled}:
        print(f"monfile: folder {folder_id!r} is disabled (enabled = false); enable it to retry its files",
              file=sys.stderr)
        return EXIT_ERROR
    if folder_id not in {f.id for f in cfg.folders}:
        print(f"monfile: unknown folder id {folder_id!r}", file=sys.stderr)
        return EXIT_ERROR
    store = _open_store(cfg, readonly=False)
    if store is None:
        return EXIT_ERROR
    try:
        with store.tx():
            rec = store.get(folder_id, rel_path)
            if rec is None or rec.status != "FAILED":
                state = rec.status if rec else "not tracked"
                print(f"monfile: {folder_id}/{rel_path} is {state}; only FAILED files can be retried", file=sys.stderr)
                return EXIT_ERROR
            if rec.gone:
                print(f"monfile: {folder_id}/{rel_path} no longer exists in the watch folder", file=sys.stderr)
                return EXIT_ERROR
            store.put(rec.with_(status="READY", last_error=None))
    finally:
        store.close()
    folder = next(f for f in cfg.folders if f.id == folder_id)
    audit.emit(EventID.RETRY_REQUESTED, folder=folder_id, path=folder.path / rel_path, user=os.getuid(),
               sudo_user=os.environ.get("SUDO_USER"))
    print(f"monfile: {folder_id}/{rel_path} queued for retry on the next scan cycle")
    return EXIT_OK

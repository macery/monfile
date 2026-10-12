"""Command-line entry point (DESIGN §11)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from monfile import __version__

DEFAULT_CONFIG = Path("/etc/monfile/monfile.toml")


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
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run as a daemon, scanning every scan_interval_seconds")
    run.add_argument("--dry-run", action="store_true", help="log intended actions without performing them")

    scan = sub.add_parser("scan", help="run scan cycle(s) and exit (for systemd timer/cron)")
    scan.add_argument("--once", action="store_true", required=True, help="run exactly one cycle")
    scan.add_argument("--dry-run", action="store_true", help="log intended actions without performing them")

    sub.add_parser("validate-config", help="validate config contents and permissions, then exit")
    sub.add_parser("status", help="show tracked files and their states")

    retry = sub.add_parser("retry", help="return a FAILED file to READY")
    retry.add_argument("folder_id")
    retry.add_argument("rel_path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # TODO: dispatch to config.load_config → state/scanner/stability/executor (DESIGN §3).
    print(f"monfile: command '{args.command}' is not implemented yet", file=sys.stderr)
    return 2

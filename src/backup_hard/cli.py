"""Unified command-line interface for backup-hard."""

from __future__ import annotations

import argparse
import sys
from importlib import import_module


COMMAND_MODULES = {
    "inventory": "backup_hard.inventory",
    "plan": "backup_hard.select_formats",
    "copy": "backup_hard.copy_backup",
    "verify": "backup_hard.verify_backup",
    "report": "backup_hard.reports",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backup-hard", description=__doc__)
    parser.add_argument("command", choices=sorted(COMMAND_MODULES))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    module = import_module(COMMAND_MODULES[options.command])
    entry = module.main
    try:
        return int(entry(options.arguments))
    except TypeError:
        # Transitional legacy commands read sys.argv and are wrapped in commit 29.
        return int(entry())


def inventory_main() -> int:
    return main(["inventory", *sys.argv[1:]])


def plan_main() -> int:
    return main(["plan", *sys.argv[1:]])


def copy_main() -> int:
    return main(["copy", *sys.argv[1:]])


def verify_main() -> int:
    return main(["verify", *sys.argv[1:]])

"""Command line entry point: every command prints exactly one JSON line."""
import argparse
import json
import sys
from pathlib import Path

from cloudfix import __version__
from cloudfix.common.settings import describe
from cloudfix.finders import gp2_to_gp3
from cloudfix.finders.gp2_to_gp3.run import run_assess, run_replay


class CliError(Exception):
    """Raised for argument errors so callers get a JSON error, not a process exit."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cloudfix", allow_abbrev=False)
    parser.add_argument("--version", action="store_true", help="print the package version as JSON")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    gp2 = commands.add_parser(gp2_to_gp3.NAME, allow_abbrev=False, help="assess EBS gp2 volumes for gp3")
    actions = gp2.add_subparsers(dest="action", metavar="ACTION", required=True)
    actions.add_parser("describe", allow_abbrev=False, help="print the finder's settings as JSON")
    assess = actions.add_parser("assess", allow_abbrev=False, help="assess volumes with read-only AWS calls")
    assess.add_argument("--config", required=True, type=Path, help="JSON file with the answered settings")
    assess.add_argument("--out", required=True, type=Path, help="new run directory to create")
    replay = actions.add_parser("replay", allow_abbrev=False, help="re-run an assessment from recorded evidence")
    replay.add_argument("--evidence", required=True, type=Path, help="evidence.json from an earlier run")
    replay.add_argument("--out", required=True, type=Path, help="new run directory to create")
    return parser


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def _error(code: str, message: str) -> int:
    _emit({"status": "error", "error": {"code": code, "message": message}})
    return 1


def _gp2_to_gp3(args: argparse.Namespace) -> int:
    if args.action == "describe":
        _emit({"finder": gp2_to_gp3.NAME, "settings": describe(gp2_to_gp3.SETTINGS)})
        return 0
    if args.action == "assess":
        return run_assess(args.config, args.out)
    return run_replay(args.evidence, args.out)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.version:
            _emit({"package": "cloudfix", "version": __version__})
            return 0
        if args.command == gp2_to_gp3.NAME:
            return _gp2_to_gp3(args)
        parser.print_usage(sys.stdout)
        return 0
    except SystemExit as exc:  # --help prints its text and exits through argparse
        return exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    except CliError as exc:
        return _error("usage", str(exc))
    except Exception as exc:  # noqa: BLE001 - the contract is one JSON error line
        return _error("internal", f"{type(exc).__name__}: {exc}")

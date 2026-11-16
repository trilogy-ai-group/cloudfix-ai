"""Command line entry point: every command prints exactly one JSON line."""
import argparse
import json
import sys

from cloudfix import __version__


class CliError(Exception):
    """Raised for argument errors so callers get a JSON error, not a process exit."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="cloudfix")
    parser.add_argument("--version", action="store_true", help="print the package version as JSON")
    return parser


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")


def _error(code: str, message: str) -> int:
    _emit({"status": "error", "error": {"code": code, "message": message}})
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.version:
            _emit({"package": "cloudfix", "version": __version__})
            return 0
        parser.print_usage(sys.stdout)
        return 0
    except CliError as exc:
        return _error("usage", str(exc))
    except Exception as exc:  # noqa: BLE001 - the contract is one JSON error line
        return _error("internal", f"{type(exc).__name__}: {exc}")

"""Command line entry point: every command prints exactly one JSON line."""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from cloudfix import __version__
from cloudfix.common.aws import RecordingAws
from cloudfix.common.errors import AwsCallError, InputError
from cloudfix.common.output import write_json
from cloudfix.common.settings import describe
from cloudfix.finders import gp2_to_gp3
from cloudfix.finders.gp2_to_gp3.run import run_assess, run_replay
from cloudfix.fixers.gp2_to_gp3 import apply as fixer_apply
from cloudfix.fixers.gp2_to_gp3 import cleanup as fixer_cleanup
from cloudfix.fixers.gp2_to_gp3 import plan as fixer_plan

FIX_NAME = "gp2-to-gp3-fix"
APPLY_EXIT = {"completed": 0, "in_progress": 3, "failed": 1}


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
    fix = commands.add_parser(FIX_NAME, allow_abbrev=False, help="plan, describe and apply gp2 to gp3 changes")
    fix_actions = fix.add_subparsers(dest="action", metavar="ACTION", required=True)
    plan = fix_actions.add_parser("plan", allow_abbrev=False, help="write an approvable plan from an assessment")
    change = fix_actions.add_parser("change", allow_abbrev=False, help="write change.json for the PR worker")
    for p in (plan, change):
        p.add_argument("--assessment", required=True, type=Path, help="run directory of a complete assessment")
        p.add_argument("--volume-id", action="append", default=[], help="volume to include (repeatable)")
        p.add_argument("--iops", action="append", default=[], metavar="VOL=N", help="IOPS override (repeatable)")
        p.add_argument("--throughput", action="append", default=[], metavar="VOL=N",
                       help="throughput override in MiB/s (repeatable)")
    plan.add_argument("--management", required=True, choices=("not-managed", "unknown"))
    plan.add_argument("--out", required=True, type=Path, help="new directory to hold plan.json")
    change.add_argument("--repo-hint")
    change.add_argument("--delivery", choices=("draft_pr", "local_patch"), default="draft_pr")
    change.add_argument("--out", required=True, type=Path, help="change.json file to create")
    apply = fix_actions.add_parser("apply", allow_abbrev=False, help="carry out an approved plan; rerun to resume")
    apply.add_argument("--plan", required=True, type=Path, help="plan.json written by plan")
    apply.add_argument("--approve", required=True, metavar="SHA256", help="the plan's SHA-256 as printed by plan")
    apply.add_argument("--approval-kind", required=True, choices=("after_plan", "prior"))
    apply.add_argument("--approval-text", required=True, help="the user's exact approval wording")
    apply.add_argument("--confirm-direct-management", action="store_true",
                       help="the user confirmed direct management for volumes whose management basis is unknown")
    apply.add_argument("--wait", type=float, default=fixer_apply.DEFAULT_WAIT_MINUTES, metavar="MINUTES",
                       help="minutes to wait for completion before reporting in progress")
    status = fix_actions.add_parser("status", allow_abbrev=False, help="report recorded and live state; reads only")
    status.add_argument("--plan", required=True, type=Path, help="plan.json written by plan")
    cleanup = fix_actions.add_parser("cleanup", allow_abbrev=False,
                                     help="delete the recovery snapshots of verified volumes; needs its own approval")
    cleanup.add_argument("--plan", required=True, type=Path, help="plan.json written by plan")
    cleanup.add_argument("--approve", required=True, metavar="SHA256", help="the plan's SHA-256 as printed by plan")
    cleanup.add_argument("--approval-kind", required=True, choices=("after_plan", "prior"))
    cleanup.add_argument("--approval-text", required=True, help="the user's exact approval wording for the cleanup")
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


def _overrides(values: list[str], flag: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        vol, sep, number = value.partition("=")
        if not sep or not vol or not number.isdigit():
            raise InputError(f"{flag} expects VOL=N with a whole number, got {value!r}")
        out[vol] = int(number)
    return out


def _fix(args: argparse.Namespace, aws=None, now=None) -> int:
    iops, throughput = _overrides(args.iops, "--iops"), _overrides(args.throughput, "--throughput")
    assessment = fixer_plan.load_assessment(args.assessment)
    ids = args.volume_id or None
    if args.action == "change":
        if args.out.exists():
            raise InputError(f"output file already exists: {args.out}")
        change = fixer_plan.make_change(assessment, volume_ids=ids, iops_overrides=iops,
                                        throughput_overrides=throughput, repo_hint=args.repo_hint,
                                        delivery=args.delivery)
        write_json(args.out, change)
        _emit({"status": "written", "change_file": str(args.out.resolve()),
               "volumes": [r["id"] for r in change["resources"]]})
        return 0
    aws = aws or RecordingAws(assessment["result"]["region"], fixer_plan.FIXER_OPS,
                              profile=assessment["profile"])
    plan = fixer_plan.make_plan(assessment, aws, volume_ids=ids, management=args.management,
                                iops_overrides=iops, throughput_overrides=throughput,
                                now=now or datetime.now(timezone.utc))
    if not plan["volumes"]:
        _emit({"status": "refused", "volumes": [], "refused": plan["refused"]})
        return 1
    path, digest = fixer_plan.write_plan(args.out, plan)
    _emit({"status": "planned", "plan_file": str(path), "sha256": digest,
           "volumes": [v["volume_id"] for v in plan["volumes"]], "refused": plan["refused"]})
    return 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _apply(args: argparse.Namespace) -> int:
    if not math.isfinite(args.wait) or args.wait < 0:
        raise InputError(f"--wait must be a non-negative number of minutes, got {args.wait}")
    plan = fixer_apply.read_plan(args.plan)
    aws = RecordingAws(plan["region"], fixer_plan.FIXER_OPS, profile=plan.get("profile"), retries=False)
    result = fixer_apply.apply_plan(args.plan, args.approve,
                                    fixer_apply.Approval(args.approval_kind, args.approval_text), aws,
                                    confirm_direct=args.confirm_direct_management, wait_minutes=args.wait,
                                    now=_utcnow, sleep=time.sleep)
    _emit(result)
    return APPLY_EXIT[result["status"]]


def _status(args: argparse.Namespace) -> int:
    plan = fixer_apply.read_plan(args.plan)
    aws = RecordingAws(plan["region"], fixer_plan.FIXER_OPS, profile=plan.get("profile"))
    _emit(fixer_cleanup.status(args.plan, aws))
    return 0


def _cleanup(args: argparse.Namespace) -> int:
    plan = fixer_apply.read_plan(args.plan)
    aws = RecordingAws(plan["region"], fixer_plan.FIXER_OPS, profile=plan.get("profile"), retries=False)
    _emit(fixer_cleanup.cleanup(args.plan, args.approve, fixer_apply.Approval(args.approval_kind, args.approval_text),
                                aws, now=_utcnow))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.version:
            _emit({"package": "cloudfix", "version": __version__})
            return 0
        if args.command == gp2_to_gp3.NAME:
            return _gp2_to_gp3(args)
        if args.command == FIX_NAME:
            handlers = {"apply": _apply, "status": _status, "cleanup": _cleanup}
            return handlers.get(args.action, _fix)(args)
        parser.print_usage(sys.stdout)
        return 0
    except SystemExit as exc:  # --help prints its text and exits through argparse
        return exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    except InputError as exc:
        return _error("invalid_input", str(exc))
    except AwsCallError as exc:
        _emit({"status": "error", "error": {"code": exc.code, "message": str(exc), "service": exc.service,
                                            "operation": exc.operation}})
        return 1
    except CliError as exc:
        return _error("usage", str(exc))
    except Exception as exc:  # noqa: BLE001 - the contract is one JSON error line
        return _error("internal", f"{type(exc).__name__}: {exc}")

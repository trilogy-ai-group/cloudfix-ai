"""Fixer `apply`: approval checks, then a resumable per-volume execution recorded in execution.json."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from cloudfix.common.aws import Caller
from cloudfix.common.errors import AwsCallError, InputError
from cloudfix.common.output import write_json
from cloudfix.fixers.gp2_to_gp3.plan import _refusal as plan_refusal  # the plan's own preconditions, re-checked
from cloudfix.fixers.gp2_to_gp3.plan import _when

STATES = ("planned", "snapshot_requested", "snapshot_completed", "modification_submitted", "optimizing",
          "completed", "verified")
TERMINAL = ("verified", "failed")
ACT_NOW = ("snapshot_completed", "completed")  # states whose next step needs no waiting on AWS
POLL_SECONDS = 15
DEFAULT_WAIT_MINUTES = 15
_PLAN_KEYS = ("plan_id", "created_at", "expires_at", "account", "region", "volumes")


@dataclass(frozen=True)
class Approval:
    kind: Literal["after_plan", "prior"]
    wording: str


def _parse_plan(data: bytes, path: Path) -> dict:
    try:
        plan = json.loads(data)
    except ValueError as e:
        raise InputError(f"cannot parse {path}: {e}") from e
    if not isinstance(plan, dict) or any(k not in plan for k in _PLAN_KEYS):
        raise InputError(f"{path} is not a fixer plan")
    return plan


def _read_bytes(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as e:
        raise InputError(f"cannot read {path}: {e}") from e


def read_plan(plan_path: Path) -> dict:
    """The plan's content, for choosing the AWS region and profile; it is not yet approved."""
    return _parse_plan(_read_bytes(plan_path), plan_path)


def _check_approval(plan: dict, approval: Approval, confirm_direct: bool) -> None:
    if approval.kind not in ("after_plan", "prior"):
        raise InputError(f"approval kind must be after_plan or prior, got {approval.kind!r}")
    if not approval.wording.strip():
        raise InputError("the approval wording is empty; pass the user's exact words")
    if approval.kind == "prior":
        bad = [v["volume_id"] for v in plan["volumes"]
               if v.get("deviates_from_assessment") or v.get("management_basis") != "not_managed_by_code"]
        if bad:
            raise InputError("an earlier approval cannot cover a volume with an override or a management basis "
                             f"other than 'not managed by code': {', '.join(bad)}; show the plan and ask")
    unconfirmed = [v["volume_id"] for v in plan["volumes"] if v.get("management_basis") != "not_managed_by_code"]
    if unconfirmed and not confirm_direct:
        raise InputError("management by code is unknown for " + ", ".join(unconfirmed)
                         + "; apply needs the user's explicit confirmation of direct management "
                         "(--confirm-direct-management)")


def _at_target(live: dict, target: dict) -> bool:
    return (live.get("VolumeType") == "gp3" and live.get("Iops") == target["iops"]
            and live.get("Throughput") == target["throughput"])


def _setting(live: dict) -> dict:
    return {"type": live.get("VolumeType"), "size_gib": live.get("Size"), "iops": live.get("Iops"),
            "throughput": live.get("Throughput")}


def _latest(items: list[dict]) -> dict | None:
    return max(items, key=lambda m: _when(m["StartTime"])) if items else None


class _Run:
    def __init__(self, plan: dict, entries: dict, execution: dict, path: Path, aws: Caller,
                 now: Callable[[], datetime]):
        self.plan, self.entries, self.execution, self.path, self.aws, self.now = (
            plan, entries, execution, path, aws, now)
        self.created = _when(plan["created_at"])
        self.prior_calls = list(execution["calls"])
        self.base = len(aws.reads)

    def save(self) -> None:
        self.execution["calls"] = self.prior_calls + list(self.aws.reads[self.base:])
        write_json(self.path, self.execution)

    def move(self, rec: dict, state: str, reason: str | None = None) -> None:
        rec["state"] = state
        rec["history"].append({"state": state, "at": self.now().isoformat()})
        if reason:
            rec["reason"] = reason
        self.save()

    def pending(self) -> list[str]:
        return [vid for vid in self.entries if self.execution["volumes"][vid]["state"] not in TERMINAL]

    def loop(self, wait_minutes: float, sleep: Callable[[float], None]) -> None:
        deadline = self.now() + timedelta(minutes=wait_minutes)
        while True:
            for vid in self.pending():
                self.advance(vid)
            if not self.pending() or self.now() >= deadline:
                return
            sleep(POLL_SECONDS)

    def advance(self, vid: str) -> None:
        rec, entry = self.execution["volumes"][vid], self.entries[vid]
        while True:
            before = rec["state"]
            getattr(self, "_" + before)(vid, entry, rec)
            if rec["state"] == before or rec["state"] not in ACT_NOW:
                return

    def tags(self, vid: str) -> list[dict]:
        return [{"Key": "cloudfix-ai:fixer", "Value": "gp2-to-gp3"},
                {"Key": "cloudfix-ai:originalResourceId", "Value": vid},
                {"Key": "cloudfix-ai:executionDate", "Value": self.now().astimezone(timezone.utc).date().isoformat()},
                {"Key": "cloudfix-ai:planId", "Value": self.plan["plan_id"]}]

    # Reads.
    def volume(self, vid: str) -> dict | None:
        out = self.aws.call("ec2", "DescribeVolumes", {"Filters": [{"Name": "volume-id", "Values": [vid]}]})
        return (out.get("Volumes") or [None])[0]

    def modifications(self, vid: str) -> list[dict]:
        try:
            out = self.aws.call("ec2", "DescribeVolumesModifications", {"VolumeIds": [vid]})
        except AwsCallError as e:
            if e.code == "InvalidVolumeModification.NotFound":
                return []
            raise
        return out.get("VolumesModifications") or []

    def ours(self, mods: list[dict], target: dict) -> dict | None:
        """The latest modification started after the plan was made that asks for exactly the planned target."""
        return _latest([m for m in mods if _when(m["StartTime"]) > self.created
                        and m.get("TargetVolumeType") == "gp3" and m.get("TargetIops") == target["iops"]
                        and m.get("TargetThroughput") == target["throughput"]])

    def tagged_snapshot(self, vid: str) -> dict | None:
        out = self.aws.call("ec2", "DescribeSnapshots", {"OwnerIds": ["self"], "Filters": [
            {"Name": "tag:cloudfix-ai:planId", "Values": [self.plan["plan_id"]]},
            {"Name": "tag:cloudfix-ai:originalResourceId", "Values": [vid]}]})
        snaps = out.get("Snapshots") or []
        return max(snaps, key=lambda s: str(s.get("StartTime") or "")) if snaps else None

    def refusal(self, entry: dict, live: dict | None, latest: dict | None) -> str | None:
        if live is None:
            return "the volume no longer exists"
        reason = plan_refusal(live, {"sizing": {"size_gib": entry["current"]["size_gib"]}}, latest, self.now())
        return f"live state no longer matches the plan: {reason}" if reason else None

    def as_is(self, rec: dict, live: dict) -> None:
        rec["already_at_target"], rec["final"] = True, _setting(live)
        self.move(rec, "verified")

    # One step per state: reconcile with AWS, then act only if AWS shows the step has not happened.
    def _planned(self, vid: str, entry: dict, rec: dict) -> None:
        live = self.volume(vid)
        if live is not None and _at_target(live, entry["target"]):
            return self.as_is(rec, live)
        if reason := self.refusal(entry, live, _latest(self.modifications(vid))):
            return self.move(rec, "failed", reason)
        snap = self.tagged_snapshot(vid)
        if snap is None:
            try:
                snap = self.aws.call("ec2", "CreateSnapshot", {
                    "VolumeId": vid, "Description": f"cloudfix-ai gp2-to-gp3 recovery point {self.plan['plan_id']}",
                    "TagSpecifications": [{"ResourceType": "snapshot", "Tags": self.tags(vid)}]})
            except AwsCallError as e:
                snap = self.tagged_snapshot(vid)  # the snapshot may exist with only the response lost
                if snap is None:
                    return self.move(rec, "failed", f"ec2.CreateSnapshot failed: {e.code}: {e.message}")
        rec["snapshot_id"] = snap["SnapshotId"]
        self.move(rec, "snapshot_requested")

    def _snapshot_requested(self, vid: str, entry: dict, rec: dict) -> None:
        sid = rec["snapshot_id"]
        try:
            snaps = self.aws.call("ec2", "DescribeSnapshots", {"SnapshotIds": [sid]}).get("Snapshots") or []
        except AwsCallError as e:
            if e.code != "InvalidSnapshot.NotFound":
                raise
            snaps = []  # a new snapshot can take a moment to become visible
        state = snaps[0].get("State") if snaps else None
        if state == "completed":
            self.move(rec, "snapshot_completed")
        elif state == "error":
            self.move(rec, "failed", f"snapshot {sid} failed: {snaps[0].get('StateMessage') or 'state error'}")

    def _snapshot_completed(self, vid: str, entry: dict, rec: dict) -> None:
        target = entry["target"]
        mods = self.modifications(vid)
        if self.ours(mods, target) is None:
            live = self.volume(vid)
            if live is not None and _at_target(live, target):
                return self.as_is(rec, live)
            if reason := self.refusal(entry, live, _latest(mods)):
                return self.move(rec, "failed", reason)
            try:
                self.aws.call("ec2", "ModifyVolume", {"VolumeId": vid, "VolumeType": "gp3",
                                                      "Iops": target["iops"], "Throughput": target["throughput"]})
            except AwsCallError as e:
                if self.ours(self.modifications(vid), target) is None:  # a lost response leaves it in history
                    return self.move(rec, "failed", f"ec2.ModifyVolume failed: {e.code}: {e.message}")
        self.move(rec, "modification_submitted")

    def _modification_submitted(self, vid: str, entry: dict, rec: dict) -> None:
        mod = self.ours(self.modifications(vid), entry["target"]) or {}
        state = mod.get("ModificationState")
        if state == "optimizing" and rec["state"] != "optimizing":
            self.move(rec, "optimizing")
        elif state == "completed":
            self.move(rec, "completed")
        elif state == "failed":
            self.move(rec, "failed", f"the modification failed: {mod.get('StatusMessage') or 'no status message'}")

    _optimizing = _modification_submitted

    def _completed(self, vid: str, entry: dict, rec: dict) -> None:
        target, live = entry["target"], self.volume(vid)
        if live is None or not _at_target(live, target):
            found = "no volume" if live is None else (
                f"{live.get('VolumeType')}, {live.get('Iops')} IOPS, {live.get('Throughput')} MiB/s")
            return self.move(rec, "failed", f"verification failed: expected gp3, {target['iops']} IOPS, "
                                            f"{target['throughput']} MiB/s; found {found}")
        rec["final"] = _setting(live)
        tagged = {t.get("Key"): t.get("Value") for t in live.get("Tags") or []}
        if tagged.get("cloudfix-ai:planId") != self.plan["plan_id"]:
            try:
                self.aws.call("ec2", "CreateTags", {"Resources": [vid], "Tags": self.tags(vid)})
            except AwsCallError as e:
                return self.move(rec, "failed", f"the volume is at the target settings, but ec2.CreateTags "
                                                f"failed: {e.code}: {e.message}")
        self.move(rec, "verified")

    def result(self) -> dict:
        volumes = {}
        for vid in self.entries:
            rec = self.execution["volumes"][vid]
            volumes[vid] = {"state": rec["state"], "snapshot_id": rec["snapshot_id"]}
            if "reason" in rec:
                volumes[vid]["reason"] = rec["reason"]
        states = [v["state"] for v in volumes.values()]
        status = ("failed" if "failed" in states
                  else "completed" if all(s == "verified" for s in states) else "in_progress")
        return {"status": status, "volumes": volumes, "execution_file": str(self.path)}


def apply_plan(plan_path: Path, approve_sha256: str, approval: Approval, aws: Caller, *, confirm_direct: bool,
               wait_minutes: float, now: Callable[[], datetime], sleep: Callable[[float], None]) -> dict:
    plan_path = Path(plan_path).resolve()
    data = _read_bytes(plan_path)
    digest = hashlib.sha256(data).hexdigest()
    if digest != approve_sha256.strip().lower():
        raise InputError(f"the plan's SHA-256 is {digest}, not the approved {approve_sha256}; "
                         "the plan changed or the approval is for another plan")
    plan = _parse_plan(data, plan_path)
    path = plan_path.with_name("execution.json")
    execution = None
    if path.exists():
        try:
            execution = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise InputError(f"cannot read {path}: {e}") from e
        if not isinstance(execution, dict) or (
                (execution.get("plan_id"), execution.get("plan_sha256")) != (plan["plan_id"], digest)):
            raise InputError(f"{path} records a different plan; it does not belong to this plan.json")
        if bad := [v for v, r in execution["volumes"].items() if r.get("state") not in STATES + ("failed",)]:
            raise InputError(f"{path} holds an unknown state for {', '.join(bad)}")
    elif now() > _when(plan["expires_at"]):
        raise InputError(f"the plan expired at {plan['expires_at']}; make a new plan")
    _check_approval(plan, approval, confirm_direct)
    entries: dict[str, dict] = {}
    for v in plan["volumes"]:
        entries.setdefault(v["volume_id"], v)  # a duplicated entry is carried out once
    if not entries:
        raise InputError("the plan has no volumes")
    if execution is None:
        execution = {"plan_id": plan["plan_id"], "plan_sha256": digest,
                     "approval": {"kind": approval.kind, "wording": approval.wording,
                                  "recorded_at": now().isoformat()},
                     "confirmed_direct_management": confirm_direct, "volumes": {}, "calls": []}
    for vid in entries:
        execution["volumes"].setdefault(vid, {"state": "planned", "snapshot_id": None, "already_at_target": False,
                                              "history": [{"state": "planned", "at": now().isoformat()}]})
    run = _Run(plan, entries, execution, path, aws, now)
    if run.pending():
        identity = aws.call("sts", "GetCallerIdentity", {})
        if identity.get("Account") != plan["account"]:
            raise InputError(f"credentials belong to account {identity.get('Account')}, "
                             f"but the plan is for account {plan['account']}")
        try:
            run.save()  # execution.json exists before the first write, so the run can resume after expiry
            run.loop(wait_minutes, sleep)
        finally:
            run.save()
    return run.result()

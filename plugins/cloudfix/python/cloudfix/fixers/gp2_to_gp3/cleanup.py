"""Fixer `status` (read only) and `cleanup` (deletes recovery snapshots that this plan created)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from cloudfix.common.aws import Caller
from cloudfix.common.errors import AwsCallError, InputError
from cloudfix.common.output import write_json
from cloudfix.fixers.gp2_to_gp3.apply import STATES, Approval, _latest, _parse_plan, _read_bytes


def _load_execution(path: Path, plan: dict, digest: str | None) -> dict | None:
    if not path.exists():
        return None
    try:
        execution = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {path}: {e}") from e
    if not isinstance(execution, dict) or execution.get("plan_id") != plan["plan_id"] or (
            digest is not None and execution.get("plan_sha256") != digest):
        raise InputError(f"{path} records a different plan; it does not belong to this plan.json")
    if bad := [v for v, r in execution["volumes"].items() if r.get("state") not in STATES + ("failed",)]:
        raise InputError(f"{path} holds an unknown state for {', '.join(bad)}")
    return execution


def _volume_ids(plan: dict) -> list[str]:
    return list(dict.fromkeys(v["volume_id"] for v in plan["volumes"]))


def _check_account(aws: Caller, plan: dict) -> None:
    identity = aws.call("sts", "GetCallerIdentity", {})
    if identity.get("Account") != plan["account"]:
        raise InputError(f"credentials belong to account {identity.get('Account')}, "
                         f"but the plan is for account {plan['account']}")


def status(plan_path: Path, aws: Caller) -> dict:
    """Recorded and live state per volume. Only reads (STS and Describe*); nothing is written."""
    plan_path = Path(plan_path).resolve()
    plan = _parse_plan(_read_bytes(plan_path), plan_path)
    path = plan_path.with_name("execution.json")
    execution = _load_execution(path, plan, None)
    _check_account(aws, plan)
    volumes = {}
    for vid in _volume_ids(plan):
        rec = (execution or {"volumes": {}})["volumes"].get(vid)
        found = aws.call("ec2", "DescribeVolumes", {"Filters": [{"Name": "volume-id", "Values": [vid]}]})
        live = (found.get("Volumes") or [None])[0]
        try:
            mods = aws.call("ec2", "DescribeVolumesModifications", {"VolumeIds": [vid]}).get(
                "VolumesModifications") or []
        except AwsCallError as e:
            if e.code != "InvalidVolumeModification.NotFound":
                raise
            mods = []
        mod = _latest(mods)
        entry = {
            "state": rec["state"] if rec else "not_started",
            "snapshot_id": rec["snapshot_id"] if rec else None,
            "snapshot_deleted": bool(rec and rec.get("snapshot_deleted")),
            "live": None if live is None else {"type": live.get("VolumeType"), "size_gib": live.get("Size"),
                                               "iops": live.get("Iops"), "throughput": live.get("Throughput")},
            "modification": None if mod is None else {"state": mod.get("ModificationState"),
                                                      "message": mod.get("StatusMessage")},
        }
        if rec and "reason" in rec:
            entry["reason"] = rec["reason"]
        volumes[vid] = entry
    return {"plan_id": plan["plan_id"], "volumes": volumes,
            "execution_file": str(path) if execution is not None else None}


def _skip_reason(rec: dict) -> str | None:
    """Eligible: verified, with a recorded snapshot that was not deleted yet."""
    if rec["state"] != "verified":
        return f"not verified (state {rec['state']})"
    if rec.get("snapshot_deleted"):
        return "the snapshot was already deleted"
    if not rec.get("snapshot_id"):
        if rec.get("already_at_target"):
            return "no recovery snapshot was taken (the volume was already at the target)"
        return "no recovery snapshot is recorded"
    return None


def cleanup(plan_path: Path, approve_sha256: str, approval: Approval, aws: Caller, *,
            now: Callable[[], datetime]) -> dict:
    plan_path = Path(plan_path).resolve()
    data = _read_bytes(plan_path)
    digest = hashlib.sha256(data).hexdigest()
    if digest != approve_sha256.strip().lower():
        raise InputError(f"the plan's SHA-256 is {digest}, not the approved {approve_sha256}; "
                         "the plan changed or the approval is for another plan")
    plan = _parse_plan(data, plan_path)
    if approval.kind not in ("after_plan", "prior"):
        raise InputError(f"approval kind must be after_plan or prior, got {approval.kind!r}")
    if not approval.wording.strip():
        raise InputError("the approval wording is empty; pass the user's exact words")
    path = plan_path.with_name("execution.json")
    execution = _load_execution(path, plan, digest)
    if execution is None:
        raise InputError(f"there is no execution.json next to {plan_path}; nothing was applied, so there is "
                         "nothing to clean up")
    _check_account(aws, plan)
    prior_calls, base = list(execution["calls"]), len(aws.reads)

    def save() -> None:
        execution["calls"] = prior_calls + list(aws.reads[base:])
        write_json(path, execution)

    deleted: list[str] = []
    skipped: dict[str, str] = {}
    # One record per cleanup run, so a rerun keeps the earlier run's approval, deletions and skips.
    record = {"approval": {"kind": approval.kind, "wording": approval.wording, "recorded_at": now().isoformat()},
              "deleted": deleted, "skipped": skipped}
    runs = execution.setdefault("cleanup", [])
    if not isinstance(runs, list):
        raise InputError(f"{path} holds a cleanup record in an unknown format")
    runs.append(record)
    for vid in _volume_ids(plan):
        rec = execution["volumes"].get(vid)
        if rec is None:
            skipped[vid] = "not verified (never applied)"
            continue
        if reason := _skip_reason(rec):
            skipped[vid] = reason
            continue
        sid = rec["snapshot_id"]
        try:
            snaps = aws.call("ec2", "DescribeSnapshots", {"SnapshotIds": [sid]}).get("Snapshots") or []
        except AwsCallError as e:
            if e.code != "InvalidSnapshot.NotFound":
                raise
            snaps = []
        if not snaps:
            skipped[vid] = f"snapshot {sid} was not found"
            continue
        tags = {t.get("Key"): t.get("Value") for t in snaps[0].get("Tags") or []}
        if tags.get("cloudfix-ai:planId") != plan["plan_id"]:
            skipped[vid] = f"snapshot {sid} carries a different cloudfix-ai:planId tag (tag mismatch)"
            continue
        try:
            aws.call("ec2", "DeleteSnapshot", {"SnapshotId": sid})
        except AwsCallError as e:
            skipped[vid] = f"ec2.DeleteSnapshot failed for {sid}: {e.code}: {e.message}"
            continue
        rec["snapshot_deleted"] = True
        deleted.append(vid)
        save()  # recorded right after each delete
    save()
    return {"status": "completed", "deleted": deleted, "skipped": skipped, "execution_file": str(path)}

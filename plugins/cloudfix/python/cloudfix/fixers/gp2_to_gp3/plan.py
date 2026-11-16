"""Fixer `plan`: turn a saved, complete assessment into an approvable plan, and write `change.json`."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import cloudfix
from cloudfix.common.aws import Caller
from cloudfix.common.errors import AwsCallError, InputError
from cloudfix.common.iac import iac_signals
from cloudfix.common.money import display_amounts, display_usd
from cloudfix.common.output import new_run_dir, write_json
from cloudfix.common.pricing import EbsPrices, PricingLookups
from cloudfix.finders.gp2_to_gp3.decide import estimate_savings

FIXER_OPS = frozenset({
    "sts.GetCallerIdentity", "ec2.DescribeVolumes", "ec2.DescribeVolumesModifications",
    "ec2.DescribeSnapshots", "ec2.CreateSnapshot", "ec2.CreateTags", "ec2.ModifyVolume",
    "ec2.DeleteSnapshot", "pricing.GetProducts",
})
PLAN_LIFETIME = timedelta(hours=24)
COOLDOWN = timedelta(hours=6)


def validate_gp3_target(size_gib: int, iops: int, throughput: int) -> None:
    if not 3000 <= iops <= 80000:
        raise InputError(f"gp3 IOPS must be 3000 to 80000, got {iops}")
    if iops > max(3000, 500 * size_gib):
        raise InputError(f"gp3 IOPS {iops} exceeds 500 IOPS per GiB for a {size_gib} GiB volume")
    if not 125 <= throughput <= 2000:
        raise InputError(f"gp3 throughput must be 125 to 2000 MiB/s, got {throughput}")
    if throughput > max(125, iops * 0.25):
        raise InputError(f"gp3 throughput {throughput} exceeds 0.25 MiB/s per IOPS at {iops} IOPS")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InputError(f"cannot read {path}: {e}") from e


def load_assessment(assessment_dir: Path) -> dict:
    root = Path(assessment_dir).resolve()
    context = _read_json(root / "context.json")
    if context.get("status") != "complete":
        raise InputError(f"assessment is not complete (status: {context.get('status')})")
    result = _read_json(root / "result.json")
    config = _read_json(root / "config.json") if (root / "config.json").is_file() else {}
    tags = {}
    if (root / "summary.json").is_file():
        tags = {r["resource_id"]: r.get("tags") or {} for r in _read_json(root / "summary.json")["resources"]}
    return {"path": str(root), "result": result, "config": config, "context": context, "tags": tags}


def _when(value: Any) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _tags(volume: dict) -> dict[str, str]:
    return {t.get("Key"): t.get("Value") for t in volume.get("Tags") or []}


def _latest_modification(aws: Caller, volume_id: str) -> dict | None:
    try:
        out = aws.call("ec2", "DescribeVolumesModifications", {"VolumeIds": [volume_id]})
    except AwsCallError as e:
        if e.code == "InvalidVolumeModification.NotFound":
            return None
        raise
    mods = out.get("VolumesModifications") or []
    return max(mods, key=lambda m: _when(m["StartTime"])) if mods else None


def _refusal(volume: dict, assessed: dict, mod: dict | None, now: datetime) -> str | None:
    tags = _tags(volume)
    if "cloudfix:dontFixIt" in tags:
        return "carries the cloudfix:dontFixIt tag"
    if signals := iac_signals(tags):
        return ("managed by code: " + "; ".join(signals)
                + "; the PR worker owns this volume, change it in the code, not directly")
    if volume.get("VolumeType") != "gp2":
        return f"type is {volume.get('VolumeType')}, not gp2"
    if volume.get("Size") != assessed["sizing"]["size_gib"]:
        return f"size {volume.get('Size')} GiB differs from the assessed {assessed['sizing']['size_gib']} GiB"
    if volume.get("State") not in ("in-use", "available"):
        return f"state is {volume.get('State')}, not in-use or available"
    if mod is not None:
        state = mod.get("ModificationState")
        if state in ("modifying", "optimizing"):
            return f"a modification is in progress (state {state})"
        if now - _when(mod["StartTime"]) < COOLDOWN:
            return "last modified less than 6 hours ago"
    return None


def _attachment(volume: dict) -> dict | None:
    attachments = volume.get("Attachments") or []
    if not attachments:
        return None
    a = attachments[0]
    return {"instance_id": a.get("InstanceId"), "device": a.get("Device"), "state": a.get("State")}


def _steps(vid: str, target: dict, plan_id: str) -> list[str]:
    return [
        f"ec2.CreateSnapshot for {vid} with tags cloudfix-ai:fixer=gp2-to-gp3, cloudfix-ai:originalResourceId={vid}, "
        f"cloudfix-ai:executionDate=<UTC date>, cloudfix-ai:planId={plan_id}",
        "wait for the snapshot to complete (ec2.DescribeSnapshots)",
        f"ec2.ModifyVolume {vid} to VolumeType=gp3, Iops={target['iops']}, Throughput={target['throughput']}",
        "wait for the modification to complete (ec2.DescribeVolumesModifications)",
        f"verify type, IOPS and throughput of {vid} (ec2.DescribeVolumes)",
        f"ec2.CreateTags on {vid} with the same cloudfix-ai:* tags",
    ]


def _overrides(ids: list[str], iops: dict[str, int], throughput: dict[str, int]) -> None:
    for kind, values in (("--iops", iops), ("--throughput", throughput)):
        for vid in values:
            if vid not in ids:
                raise InputError(f"{kind} names {vid}, which is not a volume being planned")


def _select(assessment: dict, volume_ids: list[str] | None) -> tuple[list[dict], list[dict]]:
    resources = {r["resource_id"]: r for r in assessment["result"]["resources"]}
    ids = list(volume_ids) if volume_ids else [i for i, r in resources.items() if r["approved"]]
    if not ids:
        raise InputError("the assessment approved no volumes")
    unapproved = [{"volume_id": i, "reason": "not approved in the assessment"}
                  for i in ids if i not in resources or not resources[i]["approved"]]
    return [resources[i] for i in ids if i not in {u["volume_id"] for u in unapproved}], unapproved


def _target(resource: dict, vid: str, iops: dict[str, int], throughput: dict[str, int]) -> tuple[dict, dict, bool]:
    s = resource["sizing"]
    assessed = {"iops": s["gp3_iops"], "throughput": s["gp3_throughput"]}
    target = {"type": "gp3", "size_gib": s["size_gib"],
              "iops": iops.get(vid, assessed["iops"]), "throughput": throughput.get(vid, assessed["throughput"])}
    deviates = vid in iops or vid in throughput
    if deviates:
        validate_gp3_target(target["size_gib"], target["iops"], target["throughput"])
    return target, assessed, deviates


def make_plan(assessment: dict, aws: Caller, *, volume_ids: list[str] | None,
              management: Literal["not-managed", "unknown"], iops_overrides: dict[str, int],
              throughput_overrides: dict[str, int], now: datetime) -> dict:
    result = assessment["result"]
    config = assessment.get("config") or {}
    chosen = list(volume_ids) if volume_ids else [r["resource_id"] for r in result["resources"] if r["approved"]]
    _overrides(chosen, iops_overrides, throughput_overrides)
    candidates, refused = _select(assessment, volume_ids)
    prices = {t: EbsPrices(**p) for t, p in (result.get("prices") or {}).items()}
    lookups = PricingLookups(aws, result["region"])
    plan_id = uuid.uuid4().hex
    volumes = []
    for resource in candidates:
        vid = resource["resource_id"]
        found = aws.call("ec2", "DescribeVolumes", {"Filters": [{"Name": "volume-id", "Values": [vid]}]})
        live = (found.get("Volumes") or [None])[0]
        if live is None:
            refused.append({"volume_id": vid, "reason": "volume no longer exists"})
            continue
        reason = _refusal(live, resource, _latest_modification(aws, vid), now)
        if reason:
            refused.append({"volume_id": vid, "reason": reason})
            continue
        target, assessed, deviates = _target(resource, vid, iops_overrides, throughput_overrides)
        amounts = resource["estimate"]
        if deviates:
            est, _ = estimate_savings(resource["annual_cost"], target["size_gib"], resource["sizing"]["gp2_iops"],
                                      target["iops"], target["throughput"], prices)
            if est is None:
                raise InputError(f"cannot recompute the estimate for {vid}")
            amounts = est.as_dict()
        bound = target["size_gib"] * lookups.snapshot_gb_month()
        volumes.append({
            "volume_id": vid,
            "current": {"type": live["VolumeType"], "size_gib": live["Size"], "iops": live.get("Iops"),
                        "throughput": live.get("Throughput"), "state": live["State"]},
            "target": target, "assessed_target": assessed, "deviates_from_assessment": deviates,
            "attachment": _attachment(live),
            "estimate": {**amounts, "display": display_amounts(amounts)},
            "management_basis": "not_managed_by_code" if management == "not-managed" else "unknown",
            "snapshot_cost_upper_bound_monthly": bound, "snapshot_cost_display": display_usd(bound),
            "steps": _steps(vid, target, plan_id),
        })
    return {
        "plan_id": plan_id, "created_at": now.isoformat(), "expires_at": (now + PLAN_LIFETIME).isoformat(),
        "assessment": assessment["path"], "account": result["account"], "region": result["region"],
        "profile": config.get("profile"), "volumes": volumes, "refused": refused,
        "pricing_lookups": lookups.records, "reads": list(aws.reads),
    }


def write_plan(out_dir: Path, plan: dict) -> tuple[Path, str]:
    path = new_run_dir(out_dir) / "plan.json"
    write_json(path, plan)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def make_change(assessment: dict, *, volume_ids: list[str] | None, iops_overrides: dict[str, int],
                throughput_overrides: dict[str, int], repo_hint: str | None,
                delivery: Literal["draft_pr", "local_patch"]) -> dict:
    candidates, refused = _select(assessment, volume_ids)
    if refused:
        raise InputError("not approved in the assessment: " + ", ".join(r["volume_id"] for r in refused))
    _overrides([r["resource_id"] for r in candidates], iops_overrides, throughput_overrides)
    resources = []
    for r in candidates:
        vid, s = r["resource_id"], r["sizing"]
        target, assessed, deviates = _target(r, vid, iops_overrides, throughput_overrides)
        resources.append({
            "id": vid,
            "current": {"type": "gp2", "size_gib": s["size_gib"], "iops": s["gp2_iops"]},
            "target": target, "assessed_target": assessed, "deviates_from_assessment": deviates,
            "iac_evidence": iac_signals(assessment["tags"].get(vid) or {}),
        })
    note = Path(cloudfix.__file__).resolve().parents[2] / "skills/co-gp2-to-gp3-fix/iac.md"
    return {
        "finder": assessment["result"]["finder"], "account": assessment["result"]["account"],
        "region": assessment["result"]["region"], "resources": resources, "assessment": assessment["path"],
        "change_note": str(note), "repo_hint": repo_hint, "delivery": delivery,
    }

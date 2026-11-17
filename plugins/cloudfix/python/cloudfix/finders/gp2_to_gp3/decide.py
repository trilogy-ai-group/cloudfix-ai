"""Pure GP2 to GP3 decisions: sizing, checks and savings. No AWS calls."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cloudfix.common.money import AMOUNT_FIELDS
from cloudfix.common.pricing import EbsPrices, annual_price

UTC = timezone.utc
ELASTIC_SUPPORT_DATE = datetime(2016, 12, 3, 23, 40, tzinfo=UTC)
_EMR_TAG = "aws:elasticmapreduce:instance-group-role"


@dataclass(frozen=True)
class Sizing:
    size_gib: int
    gp2_iops: int
    gp3_iops: int
    provisioned_iops: int
    gp2_throughput: int
    gp3_throughput: int
    provisioned_throughput: int


def size_gp3(size_gib: int) -> Sizing:
    gp2_iops = min(16000, max(100, 3 * size_gib))
    gp3_iops = min(80000, max(gp2_iops, 3000))
    gp2_throughput = math.ceil(min(250, gp2_iops / 4))
    gp3_throughput = min(2000, max(gp2_throughput, 125))
    return Sizing(size_gib, gp2_iops, gp3_iops, max(0, gp2_iops - 3000),
                  gp2_throughput, gp3_throughput, max(0, gp3_throughput - 125))


@dataclass(frozen=True)
class Estimate:
    current_annual_cost: float
    proposed_annual_cost: float
    annual_saving: float
    public_current_annual_cost: float
    public_proposed_annual_cost: float
    public_annual_saving: float

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in AMOUNT_FIELDS}


assert tuple(f.name for f in fields(Estimate)) == AMOUNT_FIELDS


def estimate_savings(annual_cost: float, size_gib: int, gp2_iops: int, target_iops: int,
                     target_throughput: int, prices: Mapping[str, EbsPrices]) -> tuple[Estimate | None, dict]:
    gp2 = annual_price(prices["gp2"], size_gib, gp2_iops, 0)
    gp3 = annual_price(prices["gp3"], size_gib, target_iops, target_throughput)
    breakdown = {"gp2": asdict(gp2), "gp3": asdict(gp3)}
    if gp2.annual <= 0:
        return None, breakdown
    proposed = annual_cost * gp3.annual / gp2.annual
    return Estimate(annual_cost, proposed, annual_cost - proposed,
                    gp2.annual, gp3.annual, gp2.annual - gp3.annual), breakdown


@dataclass
class Check:
    name: str
    passed: bool
    details: dict = field(default_factory=dict)


@dataclass
class ResourceResult:
    resource_id: str
    checks: list[Check]
    fail_fast: bool
    approved: bool
    sizing: Sizing | None
    estimate: Estimate | None
    public_breakdown: dict | None
    annual_cost: float | None
    public_annual_cost: float | None


def _after(value: Any) -> bool:
    return isinstance(value, datetime) and value > ELASTIC_SUPPORT_DATE


def _tag_map(resource: Mapping) -> dict[str, str]:
    return {t.get("Key"): t.get("Value") for t in resource.get("Tags") or []}


def _fail_fast_checks(resource_id: str, volume: dict | None) -> list[Check]:
    """Evaluate the fail-fast checks in order, stopping after the first failure."""
    vtype = (volume or {}).get("VolumeType")
    tag = _tag_map(volume or {}).get("cloudfix:dontFixIt")
    steps = (
        ("ResourceExists", lambda: volume is not None, {"resource_id": resource_id}),
        ("DontFixItTag", lambda: not tag, {"value": tag}),
        ("ResourceFixed", lambda: vtype != "gp3", {"volume_type": vtype}),
        ("VolumeExpectedType", lambda: vtype == "gp2", {"volume_type": vtype}),
    )
    checks: list[Check] = []
    for name, passes, details in steps:
        checks.append(Check(name, passes(), details))
        if not checks[-1].passed:
            break
    return checks


def _age_check(volume: dict, config: Mapping, now: datetime) -> Check:
    threshold = config["volume_age_threshold_days"]
    created = volume.get("CreateTime")
    if created is None:
        return Check("VolumeAgeXDays", False, {"reason": "create-time-unreported", "threshold_days": threshold})
    age = (now - created) // timedelta(days=1)
    return Check("VolumeAgeXDays", age >= threshold, {"age_days": age, "threshold_days": threshold})


def _instance_check(attachments: list, instances: Mapping[str, dict]) -> Check:
    if not attachments:
        return Check("EMRClusterAssociation", True, {"attached": False})
    instance_id = attachments[0].get("InstanceId")
    inst = instances.get(instance_id)
    if inst is None:
        return Check("Ec2InstanceDescribable", False, {"instance_id": instance_id})
    emr = _EMR_TAG in _tag_map(inst)
    return Check("EMRClusterAssociation", not emr, {"instance_id": instance_id})


def _elastic_check(volume: dict, attachments: list, instances: Mapping[str, dict]) -> Check:
    created, attached, launched = volume.get("CreateTime"), None, None
    if attachments:
        attached = attachments[0].get("AttachTime")
        launched = (instances.get(attachments[0].get("InstanceId")) or {}).get("LaunchTime")
    if _after(created) or _after(attached):
        ok = True
    elif not attachments:
        ok = True
    else:
        ok = _after(launched)
    return Check("ElasticVolumeEnabled", ok, {
        "support_date": ELASTIC_SUPPORT_DATE, "create_time": created,
        "attach_time": attached, "launch_time": launched})


def assess_volume(resource_id: str, volume: dict | None, instances: Mapping[str, dict],
                  annual_cost: float | None, public_annual_cost: float | None,
                  prices: Mapping[str, EbsPrices] | None, config: Mapping[str, Any],
                  now: datetime) -> ResourceResult:
    checks = _fail_fast_checks(resource_id, volume)
    if not checks[-1].passed:
        return ResourceResult(resource_id, checks, True, False, None, None, None,
                              annual_cost, public_annual_cost)
    assert volume is not None
    attachments = volume.get("Attachments") or []
    sizing = size_gp3(volume["Size"])
    estimate, breakdown = None, None
    if prices is not None and annual_cost is not None:
        estimate, breakdown = estimate_savings(annual_cost, sizing.size_gib, sizing.gp2_iops,
                                               sizing.gp3_iops, sizing.gp3_throughput, prices)
    checks.append(_age_check(volume, config, now))
    checks.append(_instance_check(attachments, instances))
    checks.append(_elastic_check(volume, attachments, instances))
    if estimate is None:
        reason = {"reason": "savings_not_finite"}
        checks.append(Check("MinimalSavings", False, dict(reason)))
        checks.append(Check("MaximumSavings", False, dict(reason)))
    else:
        minimum = config["minimal_savings_usd"]
        checks.append(Check("MinimalSavings", estimate.annual_saving > minimum,
                            {"annual_saving": estimate.annual_saving, "minimal_savings_usd": minimum}))
        checks.append(Check("MaximumSavings", estimate.annual_saving <= annual_cost,
                            {"annual_saving": estimate.annual_saving, "annual_cost": annual_cost}))
    return ResourceResult(resource_id, checks, False, all(c.passed for c in checks), sizing,
                          estimate, breakdown, annual_cost, public_annual_cost)

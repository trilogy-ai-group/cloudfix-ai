"""GP2 evidence collection: the AWS reads the decisions need, in a fixed order."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Iterator, Literal, Mapping

from cloudfix.common.aws import Caller
from cloudfix.common.errors import InputError
from cloudfix.common.pricing import EbsPrices, PricingLookups, annual_price

BATCH = 200
_ACCOUNT = re.compile(r"[0-9]{12}")


@dataclass
class Collected:
    account: str
    identity: dict
    region: str
    selected_ids: list[str]
    discovery: Literal["selected_ids", "gp2_inventory"]
    volumes: dict[str, dict | None]
    instances: dict[str, dict]
    prices: dict[str, EbsPrices] | None
    costs: dict[str, tuple[float, float]]


def collect(aws: Caller, config: Mapping[str, Any], cost_input: dict | None
            ) -> tuple[Collected, PricingLookups | None]:
    aws = _ObjectOutputs(aws)
    region = config["region"]
    ids = list(config["volume_ids"] or [])  # a copy: load_config hands out the shared default list
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise InputError("volume_ids must be distinct, non-empty volume ids")
    supplied = _supplied_costs(cost_input) if config["cost_basis"] == "supplied" else None
    if supplied is None and cost_input is not None:
        raise InputError("supplied costs are only used with cost_basis supplied")

    identity = aws.call("sts", "GetCallerIdentity", {})
    account = identity.get("Account")
    if not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
        raise InputError("GetCallerIdentity returned no account id")
    if config["account"] is not None and config["account"] != account:
        raise InputError(f"authenticated account {account} does not match the configured account "
                         f"{config['account']}")

    found = _describe_volumes(aws, ids)
    selected = ids or list(found)
    volumes = {vid: found.get(vid) for vid in selected}
    if supplied is not None:
        missing = [vid for vid in selected if vid not in supplied]
        if missing:
            raise InputError(f"no supplied annual cost for selected volume(s): {', '.join(missing)}")
    instances = _describe_instances(aws, volumes.values())

    lookups, prices = None, None
    if any(v is not None for v in volumes.values()):
        lookups = PricingLookups(aws, region)
        prices = {"gp2": lookups.ebs("gp2"), "gp3": lookups.ebs("gp3")}
    if supplied is not None:
        costs = {vid: supplied[vid] for vid in selected}
    else:
        costs = {}
        for vid, v in volumes.items():
            if v is not None:
                public = annual_price(prices["gp2"], v["Size"], 0, 0).annual
                costs[vid] = (public, public)
    discovery = "selected_ids" if ids else "gp2_inventory"
    return Collected(account, identity, region, selected, discovery, volumes, instances, prices, costs), lookups


class _ObjectOutputs:
    """Delegates to a caller and refuses any output that is not a JSON object (edited evidence)."""

    def __init__(self, aws: Caller):
        self._aws = aws

    @property
    def reads(self) -> list[dict]:
        return self._aws.reads

    def call(self, service: str, operation: str, params: dict) -> dict:
        output = self._aws.call(service, operation, params)
        if not isinstance(output, dict):
            raise InputError(f"{service}.{operation} output is not a JSON object")
        return output


def _objects(value: Any, what: str, *, required: bool = False) -> list[dict]:
    """A list of JSON objects; an absent optional list is empty."""
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise InputError(f"{what} must be a list of objects")
    return value


def _check_tags(resource: dict, what: str) -> None:
    for tag in _objects(resource.get("Tags"), f"{what} Tags"):
        if not isinstance(tag.get("Key"), str):
            raise InputError(f"{what} Tags has an entry without a string Key")


def _pages(aws: Caller, operation: str, base: dict, key: str) -> Iterator[dict]:
    """Yield every object under `key` across NextToken pages; a repeated token is an error."""
    params, seen = base, set()
    while True:
        page = aws.call("ec2", operation, params)
        yield from _objects(page.get(key), f"{operation} {key}", required=True)
        token = page.get("NextToken")
        if not token:
            return
        if not isinstance(token, str) or token in seen:
            raise InputError(f"{operation} returned an invalid or repeated pagination token")
        seen.add(token)
        params = {**base, "NextToken": token}


def _describe_volumes(aws: Caller, ids: list[str]) -> dict[str, dict]:
    if ids:
        filters = [{"Name": "volume-id", "Values": ids[i:i + BATCH]} for i in range(0, len(ids), BATCH)]
    else:
        filters = [{"Name": "volume-type", "Values": ["gp2"]}]
    found: dict[str, dict] = {}
    for f in filters:
        for v in _pages(aws, "DescribeVolumes", {"Filters": [f]}, "Volumes"):
            vid = v.get("VolumeId")
            if not isinstance(vid, str):
                raise InputError("DescribeVolumes returned a volume without an id")
            if vid in found:
                raise InputError(f"DescribeVolumes returned {vid} more than once")
            found[vid] = _parse_volume(vid, v)
    return found


def _describe_instances(aws: Caller, volumes: Iterable[dict | None]) -> dict[str, dict]:
    first_attached = {
        (v.get("Attachments") or [{}])[0].get("InstanceId") for v in volumes if v is not None
    }
    ids = sorted(i for i in first_attached if isinstance(i, str))
    instances: dict[str, dict] = {}
    for start in range(0, len(ids), BATCH):
        base = {"Filters": [{"Name": "instance-id", "Values": ids[start:start + BATCH]}]}
        for reservation in _pages(aws, "DescribeInstances", base, "Reservations"):
            for inst in _objects(reservation.get("Instances"), "DescribeInstances Instances"):
                iid = inst.get("InstanceId")
                if not isinstance(iid, str):
                    raise InputError("DescribeInstances returned an instance without an id")
                _check_tags(inst, iid)
                if not isinstance(inst.get("State"), (dict, type(None))):
                    raise InputError(f"{iid} State must be an object")
                instances[iid] = {**inst, "LaunchTime": parse_time(inst.get("LaunchTime"), f"{iid} LaunchTime")}
    return instances


def _parse_volume(vid: str, v: dict) -> dict:
    size = v.get("Size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise InputError(f"DescribeVolumes returned {vid} without a positive Size")
    _check_tags(v, vid)
    attachments = []
    for a in _objects(v.get("Attachments"), f"{vid} Attachments"):
        if not isinstance(a.get("InstanceId"), (str, type(None))):
            raise InputError(f"{vid} attachment InstanceId must be a string")
        attachments.append({**a, "AttachTime": parse_time(a.get("AttachTime"), f"{vid} AttachTime")})
    return {**v, "CreateTime": parse_time(v.get("CreateTime"), f"{vid} CreateTime"), "Attachments": attachments}


def parse_time(value: Any, what: str) -> datetime | None:
    """Recorded responses carry ISO strings; decisions compare timezone-aware datetimes."""
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise InputError(f"{what} is not an ISO timestamp: {value!r}") from None
    if parsed.tzinfo is None:
        raise InputError(f"{what} has no timezone: {value!r}")
    return parsed


def _supplied_costs(cost_input: Any) -> dict[str, tuple[float, float]]:
    if not isinstance(cost_input, dict):
        raise InputError("cost_basis supplied needs a costs JSON object")
    if cost_input.get("currency") != "USD":
        raise InputError("supplied costs must declare currency USD")
    for key in ("source", "period"):
        if not isinstance(cost_input.get(key), str) or not cost_input[key]:
            raise InputError(f"supplied costs need a non-empty {key}")
    rows = cost_input.get("resources")
    if not isinstance(rows, list):
        raise InputError("supplied costs need a resources list")
    costs: dict[str, tuple[float, float]] = {}
    for row in rows:
        rid = row.get("resourceId") if isinstance(row, dict) else None
        if not isinstance(rid, str) or not rid:
            raise InputError("every supplied cost needs a resourceId")
        if rid in costs:
            raise InputError(f"supplied costs list {rid} more than once")
        costs[rid] = (_amount(row, rid, "annualCost"), _amount(row, rid, "publicAnnualCost"))
    return costs


def _amount(row: dict, rid: str, key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise InputError(f"supplied {key} for {rid} must be a finite, non-negative number")
    return float(value)

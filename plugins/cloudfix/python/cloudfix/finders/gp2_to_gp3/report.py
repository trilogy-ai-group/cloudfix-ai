"""GP2 run artifacts: the result ledger, the run scope and the customer summary."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal, Mapping, Sequence

from cloudfix.common.money import AMOUNT_FIELDS, display_amounts, summary_totals
from cloudfix.finders.gp2_to_gp3 import NAME
from cloudfix.finders.gp2_to_gp3.collect import Collected
from cloudfix.finders.gp2_to_gp3.decide import ResourceResult, Sizing

LIMITATIONS = (
    "Proposed costs and savings are estimates; the current-cost basis is stated above.",
    "Instance-type support for online modification is not checked.",
    "Application performance and equivalent peak throughput are not verified.",
    "Instance state is a recorded snapshot; a stopped instance does not establish that its volume is unused "
    "or safe to modify.",
    "An approved assessment does not authorize execution.",
)
_COST_KINDS = {"public": "public_price_estimate", "supplied": "caller_supplied_costs"}


def build_result(collected: Collected | None, results: Sequence[ResourceResult]) -> dict:
    prices = collected.prices if collected else None
    return {
        "finder": NAME,
        "account": collected.account if collected else None,
        "region": collected.region if collected else None,
        "prices": {t: asdict(p) for t, p in prices.items()} if prices else None,
        "resources": [asdict(r) for r in results],
    }


def build_scope(collected: Collected | None, config: Mapping[str, Any] | None, identity: dict | None) -> dict:
    """What was assessed; if collection stopped early, what was asked for."""
    if collected is not None:
        return {"account": collected.account, "region": collected.region,
                "selected_volume_ids": list(collected.selected_ids), "discovery": collected.discovery}
    config = config or {}
    ids = list(config.get("volume_ids") or [])
    return {
        "account": config.get("account") or (identity or {}).get("Account"),
        "region": config.get("region"),
        "selected_volume_ids": ids or None,  # discovery did not finish: the selection is unknown
        "discovery": ("selected_ids" if ids else "gp2_inventory") if config else None,
    }


def build_summary(status: str, mode: Literal["live", "replay"], context: dict, collected: Collected | None,
                  results: Sequence[ResourceResult], artifacts: dict[str, str]) -> dict:
    complete = status == "complete"
    scope = context["scope"]
    resources = [_assessed(i, r, scope, collected, complete) for i, r in enumerate(results)]
    assessed = {r.resource_id for r in results}
    resources += [_not_assessed(vid, scope, collected)
                  for vid in scope["selected_volume_ids"] or [] if vid not in assessed]
    summary = {
        "status": status,
        "mode": mode,
        "assessed_at": context["as_of"],
        "scope": scope,
        "cost_basis": _cost_basis(context["config"] or {}, context["cost_input"], context["pricing_lookups"]),
        "configuration": context["config"],
        "no_candidates": complete and collected is not None and not collected.selected_ids,
        "totals": summary_totals(resources, complete),
        "resources": resources,
        "limitations": list(LIMITATIONS),
        "execution_outcome": "not_executed",
        "observed_saving": None,
        "artifacts": dict(artifacts),
    }
    if status == "needs_evidence":
        summary["missing_evidence"] = context["missing_evidence"]
    elif status == "error":
        summary["error"] = context["error"]
        summary["failed_reads"] = [r for r in context["reads"] if "error" in r]
    return summary


def _cost_basis(config: Mapping[str, Any], cost_input: Any, lookups: Sequence[dict]) -> dict:
    supplied = cost_input if config.get("cost_basis") == "supplied" and isinstance(cost_input, dict) else {}
    fallbacks = sum(1 for lookup in lookups if lookup["fallback_used"])
    return {
        "kind": _COST_KINDS.get(config.get("cost_basis")),
        "currency": "USD",
        "source": supplied.get("source"),
        "period": supplied.get("period"),
        "lookup_count": len(lookups),
        "fallback_count": fallbacks,
        "fallback_used": fallbacks > 0 if lookups else None,
        "lookup_evidence": {"artifact": "context", "json_pointer": "/pricing_lookups"},
    }


def _resource(vid: str, scope: dict, collected: Collected | None) -> dict:
    volume = collected.volumes.get(vid) if collected else None
    instances = collected.instances if collected else {}
    return {
        "resource_id": vid,
        "account": scope["account"],
        "region": scope["region"],
        "evidence_context": _evidence_context(volume, instances),
        "tags": {t.get("Key"): t.get("Value") for t in (volume or {}).get("Tags") or []},
    }


def _assessed(index: int, r: ResourceResult, scope: dict, collected: Collected | None, complete: bool) -> dict:
    if complete:
        decision = "approved" if r.approved else "rejected"
        estimate_status = "calculated" if r.estimate is not None else "not_calculated"
    else:
        decision = estimate_status = "unverified"
    return {
        **_resource(r.resource_id, scope, collected),
        "decision": decision,
        "checks": [{"name": c.name, "status": "passed" if c.passed else "failed", "details": c.details}
                   for c in r.checks],
        "proposed_settings": _proposed(r.sizing) if decision == "approved" and r.sizing else None,
        "estimate": _estimate(estimate_status, r.estimate.as_dict() if r.estimate is not None else None),
        "result_pointer": f"/resources/{index}",
    }


def _not_assessed(vid: str, scope: dict, collected: Collected | None) -> dict:
    return {
        **_resource(vid, scope, collected),
        "decision": "not_assessed",
        "checks": [],
        "proposed_settings": None,
        "estimate": _estimate("not_calculated", None),
        "result_pointer": None,
    }


def _estimate(status: str, amounts: dict | None) -> dict:
    amounts = amounts or dict.fromkeys(AMOUNT_FIELDS)
    return {"status": status, "currency": "USD", **amounts, "display": display_amounts(amounts)}


def _proposed(sizing: Sizing) -> dict:
    return {"volume_type": "gp3", "size_gib": sizing.size_gib, "iops": sizing.gp3_iops,
            "throughput_mib_per_second": sizing.gp3_throughput}


def _evidence_context(volume: dict | None, instances: Mapping[str, dict]) -> dict:
    if volume is None:
        return {"status": "unknown", "volume_state": None, "attachments": None}
    attachments = []
    for a in volume.get("Attachments") or []:
        instance_state = ((instances.get(a.get("InstanceId")) or {}).get("State") or {}).get("Name")
        attachments.append({"instance_id": a.get("InstanceId"), "attachment_state": a.get("State"),
                            "instance_state": instance_state})
    return {"status": "recorded", "volume_state": volume.get("State"), "attachments": attachments}

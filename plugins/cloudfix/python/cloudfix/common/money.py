"""Money display and summary totals. Code computes every amount; skills only report them."""
from __future__ import annotations

from typing import Mapping, Sequence

AMOUNT_FIELDS: tuple[str, ...] = (
    "current_annual_cost",
    "proposed_annual_cost",
    "annual_saving",
    "public_current_annual_cost",
    "public_proposed_annual_cost",
    "public_annual_saving",
)


def display_usd(value: float | None) -> str | None:
    if value is None:
        return None
    if value != 0 and abs(value) < 0.005:
        return "Positive, less than $0.01" if value > 0 else "Negative, less than $0.01"
    sign = "-" if value < 0 and round(abs(value), 2) != 0 else ""
    return f"{sign}${abs(value):,.2f}"


def display_amounts(amounts: Mapping[str, float | None]) -> dict[str, str | None]:
    return {k: display_usd(v) for k, v in amounts.items()}


def summary_totals(resources: Sequence[Mapping], complete: bool) -> dict:
    included = [
        r for r in resources
        if r["decision"] == "approved" and r["estimate"]["status"] == "calculated"
    ]
    excluded = [r for r in resources if r["decision"] == "rejected"]
    unknown = len(resources) - len(included) - len(excluded)
    if not complete:
        status = "unverified"
    elif unknown > 0:
        status = "partial"
    elif not resources:
        status = "empty"
    elif not included:
        status = "no_approved_candidates"
    else:
        status = "calculated"
    if complete and (unknown == 0 or included):
        amounts: dict[str, float | None] = {
            f: sum((r["estimate"][f] for r in included), 0.0) for f in AMOUNT_FIELDS
        }
    else:
        amounts = {f: None for f in AMOUNT_FIELDS}
    return {
        "scope": "approved_calculated_candidates",
        "status": status,
        "currency": "USD",
        "included_resource_count": len(included),
        "excluded_resource_count": len(excluded),
        "unknown_resource_count": unknown,
        **amounts,
        "display": display_amounts(amounts),
    }

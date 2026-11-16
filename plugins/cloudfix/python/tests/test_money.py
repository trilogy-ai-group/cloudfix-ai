from cloudfix.common.money import display_usd, summary_totals


def test_display_usd():
    assert display_usd(None) is None
    assert display_usd(0) == "$0.00"
    assert display_usd(1234.5) == "$1,234.50"
    assert display_usd(-24) == "-$24.00"
    assert display_usd(0.004) == "Positive, less than $0.01"
    assert display_usd(-0.004) == "Negative, less than $0.01"


def _row(decision, status, saving=1.0):
    amounts = {f: (saving if status == "calculated" else None) for f in
               ("current_annual_cost", "proposed_annual_cost", "annual_saving",
                "public_current_annual_cost", "public_proposed_annual_cost", "public_annual_saving")}
    return {"decision": decision, "estimate": {"status": status, **amounts}}


def test_totals_statuses_and_sums():
    assert summary_totals([], True)["status"] == "empty"
    assert summary_totals([_row("rejected", "calculated")], True)["status"] == "no_approved_candidates"
    t = summary_totals([_row("approved", "calculated", 0.1), _row("approved", "calculated", 0.2)], True)
    assert t["status"] == "calculated" and t["annual_saving"] == 0.1 + 0.2  # unrounded sum
    assert t["included_resource_count"] == 2 and t["display"]["annual_saving"] == "$0.30"
    p = summary_totals([_row("approved", "calculated"), _row("not_assessed", "not_calculated")], True)
    assert p["status"] == "partial" and p["unknown_resource_count"] == 1 and p["annual_saving"] == 1.0
    u = summary_totals([_row("approved", "calculated")], False)
    assert u["status"] == "unverified" and u["annual_saving"] is None

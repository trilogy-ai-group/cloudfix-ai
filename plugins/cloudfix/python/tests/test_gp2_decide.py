from datetime import datetime, timezone
import pytest
from cloudfix.common.pricing import EbsPrices
from cloudfix.finders.gp2_to_gp3.decide import size_gp3, estimate_savings, assess_volume
from tests.factories import volume, attachment, instance

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
PRICES = {"gp2": EbsPrices(0.10, 0, 0, 0, 0), "gp3": EbsPrices(0.08, 0.005, 0.04, 3000, 125)}
CFG = {"volume_age_threshold_days": 7, "minimal_savings_usd": 0}

@pytest.mark.parametrize("size,gp2_iops,gp3_iops,gp2_thr,gp3_thr", [
    (1, 100, 3000, 25, 125), (33, 100, 3000, 25, 125), (34, 102, 3000, 26, 125),
    (170, 510, 3000, 128, 128), (334, 1002, 3000, 250, 250), (1000, 3000, 3000, 250, 250),
    (5334, 16000, 16000, 250, 250), (16384, 16000, 16000, 250, 250)])
def test_size_gp3(size, gp2_iops, gp3_iops, gp2_thr, gp3_thr):
    s = size_gp3(size)
    assert (s.gp2_iops, s.gp3_iops, s.gp2_throughput, s.gp3_throughput) == (gp2_iops, gp3_iops, gp2_thr, gp3_thr)

def test_estimate_public_and_supplied():
    est, _ = estimate_savings(1200.0, 1000, 3000, 3000, 250, PRICES)
    assert (est.current_annual_cost, est.proposed_annual_cost, est.annual_saving) == pytest.approx((1200, 1020, 180))
    est, _ = estimate_savings(600.0, 1000, 3000, 3000, 250, PRICES)
    assert (est.proposed_annual_cost, est.annual_saving) == pytest.approx((510, 90))
    assert est.public_annual_saving == pytest.approx(180)

def test_zero_gp2_price_means_no_estimate():
    est, _ = estimate_savings(100.0, 100, 300, 3000, 125, {**PRICES, "gp2": EbsPrices(0, 0, 0, 0, 0)})
    assert est is None

def test_approved_volume_has_all_checks_and_settings():
    r = assess_volume("vol-1", volume("vol-1", size=100), {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert r.approved and [c.name for c in r.checks] == ["ResourceExists", "DontFixItTag", "ResourceFixed",
        "VolumeExpectedType", "VolumeAgeXDays", "EMRClusterAssociation", "ElasticVolumeEnabled",
        "MinimalSavings", "MaximumSavings"]
    assert (r.sizing.gp3_iops, r.sizing.gp3_throughput) == (3000, 125)

def test_fail_fast_stops_at_first_failure():
    r = assess_volume("vol-1", volume("vol-1", tags={"cloudfix:dontFixIt": "yes"}), {}, 1.0, 1.0, PRICES, CFG, NOW)
    assert r.fail_fast and [c.name for c in r.checks] == ["ResourceExists", "DontFixItTag"] and r.sizing is None
    assert assess_volume("vol-x", None, {}, None, None, PRICES, CFG, NOW).checks[0].passed is False
    r = assess_volume("vol-1", volume("vol-1", vtype="gp3"), {}, 1.0, 1.0, PRICES, CFG, NOW)
    assert [c.name for c in r.checks][-1] == "ResourceFixed"

def test_age_boundary_and_missing_create_time():
    young = assess_volume("v", volume("v", create_time="2026-11-09T00:00:01Z"), {}, 120.0, 120.0, PRICES, CFG, NOW)
    old = assess_volume("v", volume("v", create_time="2026-11-09T00:00:00Z"), {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert _check(young, "VolumeAgeXDays").passed is False and _check(old, "VolumeAgeXDays").passed is True
    v = volume("v"); del v["CreateTime"]
    c = _check(assess_volume("v", v, {}, 120.0, 120.0, PRICES, {**CFG, "volume_age_threshold_days": 0}, NOW), "VolumeAgeXDays")
    assert c.passed is False and c.details["reason"] == "create-time-unreported"

def test_emr_and_undescribable_instance():
    v = volume("v", attachments=[attachment("i-1")])
    emr = instance("i-1", tags={"aws:elasticmapreduce:instance-group-role": "CORE"})
    assert _check(assess_volume("v", v, {"i-1": emr}, 120.0, 120.0, PRICES, CFG, NOW), "EMRClusterAssociation").passed is False
    r = assess_volume("v", v, {}, 120.0, 120.0, PRICES, CFG, NOW)
    assert _check(r, "Ec2InstanceDescribable").passed is False and not r.approved

def test_other_emr_tag_does_not_count_as_emr():
    v = volume("v", attachments=[attachment("i-1")])
    other = instance("i-1", tags={"aws:elasticmapreduce:job-flow-id": "j-1"})
    assert _check(assess_volume("v", v, {"i-1": other}, 120.0, 120.0, PRICES, CFG, NOW), "EMRClusterAssociation").passed is True

def test_elastic_support_dates():
    v = volume("v", create_time="2016-01-01T00:00:00Z", attachments=[attachment("i-1", attach_time="2016-06-01T00:00:00Z")])
    assert _check(assess_volume("v", v, {"i-1": instance("i-1", launch_time="2015-01-01T00:00:00Z")}, 120.0, 120.0, PRICES, CFG, NOW), "ElasticVolumeEnabled").passed is False
    assert _check(assess_volume("v", v, {"i-1": instance("i-1", launch_time="2017-01-01T00:00:00Z")}, 120.0, 120.0, PRICES, CFG, NOW), "ElasticVolumeEnabled").passed is True

def test_minimal_savings_is_strict():
    r = assess_volume("v", volume("v", size=100), {}, 120.0, 120.0, PRICES, {**CFG, "minimal_savings_usd": 24}, NOW)
    assert _check(r, "MinimalSavings").passed is False  # saving == 24, not > 24

def _check(result, name):
    return next(c for c in result.checks if c.name == name)

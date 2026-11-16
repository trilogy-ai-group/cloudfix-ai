import pytest
from cloudfix.common.pricing import EbsPrices, annual_price, PricingLookups, DEFAULTS
from cloudfix.common.aws import ReplayAws

GP2 = EbsPrices(0.10, 0.0, 0.0, 0, 0)
GP3 = EbsPrices(0.08, 0.005, 0.04, 3000, 125)

def test_annual_price_gp2_and_gp3():
    assert annual_price(GP2, 1000, 3000, 250).annual == pytest.approx(1200.0)
    b = annual_price(GP3, 1000, 3000, 250)
    assert (b.iops_units, b.throughput_units) == (0, 125)
    assert b.annual == pytest.approx(1020.0)
    assert annual_price(GP3, 100, 4000, 125).iops_cost == pytest.approx(60.0)

from tests.factories import price_row
R = "eu-west-1"

def _q(*filters):
    return {"ServiceCode": "AmazonEC2", "FormatVersion": "aws_v1", "MaxResults": 100,
            "Filters": [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters]}

def _resp(query, rows):
    return {"service": "pricing", "operation": "GetProducts", "input": query, "output": {"PriceList": rows}}

STORAGE_GP2 = _q(("volumeType", "General Purpose"), ("volumeApiName", "gp2"), ("regionCode", R))
STORAGE_GP3 = _q(("volumeType", "General Purpose"), ("volumeApiName", "gp3"), ("regionCode", R))
IOPS_GP3 = _q(("volumeApiName", "gp3"), ("group", "EBS IOPS"), ("regionCode", R))
THR_GP3 = _q(("volumeApiName", "gp3"), ("group", "EBS Throughput"), ("regionCode", R))
SNAPSHOT = _q(("productFamily", "Storage Snapshot"), ("regionCode", R))

def test_lookups_parse_prices_and_record_them():
    aws = ReplayAws([_resp(STORAGE_GP3, [price_row("GB-Mo", "0.088")]),
                     _resp(IOPS_GP3, [price_row("IOPS-Mo", "0.0055")]),
                     _resp(THR_GP3, [price_row("GiBps-mo", "45.056")])])
    lookups = PricingLookups(aws, R)
    p = lookups.ebs("gp3")
    assert (p.storage_gb_month, p.iops_month, p.free_iops, p.free_throughput_mibps) == (0.088, 0.0055, 3000, 125)
    assert p.throughput_mibps_month == pytest.approx(0.044)
    assert [r["fallback_used"] for r in lookups.records] == [False, False, False]
    assert lookups.records[2]["raw_price"] == 45.056 and lookups.records[2]["unit"] == "GiBps-mo"
    lookups.ebs("gp3")
    assert len(aws.reads) == 3  # cached

def test_missing_price_falls_back_and_is_flagged():
    aws = ReplayAws([_resp(STORAGE_GP3, [price_row("GB-Mo", "0.08")]), _resp(IOPS_GP3, []),
                     _resp(THR_GP3, [price_row("GiBps-mo", "40.96")]), _resp(SNAPSHOT, [])])
    lookups = PricingLookups(aws, R)
    assert lookups.ebs("gp3").iops_month == DEFAULTS["gp3"].iops_month == 0.005
    assert lookups.snapshot_gb_month() == 0.05
    assert [r["fallback_used"] for r in lookups.records] == [False, True, False, True]

def test_gp2_makes_only_the_storage_lookup():
    aws = ReplayAws([_resp(STORAGE_GP2, [price_row("GB-Mo", "0.11")])])
    p = PricingLookups(aws, R).ebs("gp2")
    assert (p.storage_gb_month, p.iops_month, p.throughput_mibps_month) == (0.11, 0.0, 0.0) and len(aws.reads) == 1

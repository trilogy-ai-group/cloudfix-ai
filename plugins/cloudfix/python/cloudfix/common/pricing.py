"""EBS and snapshot prices from the AWS Price List, with recorded fallbacks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from cloudfix.common.aws import Caller


@dataclass(frozen=True)
class EbsPrices:
    storage_gb_month: float
    iops_month: float
    throughput_mibps_month: float
    free_iops: int
    free_throughput_mibps: int


@dataclass(frozen=True)
class PriceBreakdown:
    storage_cost: float
    iops_units: float
    iops_cost: float
    throughput_units: float
    throughput_cost: float
    annual: float


DEFAULTS = {
    "gp2": EbsPrices(0.10, 0.0, 0.0, 0, 0),
    "gp3": EbsPrices(0.08, 0.005, 0.04, 3000, 125),
}
SNAPSHOT_DEFAULT_GB_MONTH = 0.05


def annual_price(prices: EbsPrices, size_gib: float, iops: float, throughput: float) -> PriceBreakdown:
    storage_cost = 12 * size_gib * prices.storage_gb_month
    iops_units = max(0, iops - prices.free_iops) if prices.iops_month > 0 else 0
    iops_cost = 12 * iops_units * prices.iops_month
    throughput_units = (
        max(0, throughput - prices.free_throughput_mibps) if prices.throughput_mibps_month > 0 else 0
    )
    throughput_cost = 12 * throughput_units * prices.throughput_mibps_month
    return PriceBreakdown(
        float(storage_cost), float(iops_units), float(iops_cost),
        float(throughput_units), float(throughput_cost),
        float(storage_cost + iops_cost + throughput_cost),
    )


def _query(*filters: tuple[str, str]) -> dict:
    return {
        "ServiceCode": "AmazonEC2",
        "FormatVersion": "aws_v1",
        "MaxResults": 100,
        "Filters": [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters],
    }


def _extract_max(price_list: list, unit: str) -> float | None:
    """The highest OnDemand price in `unit` across all rows (a conservative bound)."""
    values = [v for r in price_list if (v := _extract([r], unit)) is not None]
    return max(values) if values else None


def _extract(price_list: list, unit: str) -> float | None:
    row = next((r for r in price_list if unit in (r if isinstance(r, str) else json.dumps(r))), None)
    if row is None:
        return None
    try:
        doc = json.loads(row) if isinstance(row, str) else row
        for term in doc["terms"]["OnDemand"].values():
            for dim in term["priceDimensions"].values():
                if dim.get("unit") == unit:
                    return float(dim["pricePerUnit"]["USD"]) or None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return None


class PricingLookups:
    def __init__(self, aws: Caller, region: str):
        self._aws = aws
        self._region = region
        self._ebs: dict[str, EbsPrices] = {}
        self._snapshot: float | None = None
        self.records: list[dict] = []

    def _lookup(self, query: dict, unit: str, default: float, divisor: float = 1.0, highest: bool = False) -> float:
        out = self._aws.call("pricing", "GetProducts", query)
        raw = (_extract_max if highest else _extract)(out.get("PriceList", []), unit)
        value = default if raw is None else raw / divisor
        self.records.append({
            "query": query,
            "region": self._region,
            "unit": unit,
            "raw_price": raw,
            "value_used": value,
            "fallback_used": raw is None,
        })
        return value

    def ebs(self, volume_type: Literal["gp2", "gp3"]) -> EbsPrices:
        if volume_type in self._ebs:
            return self._ebs[volume_type]
        r, d = self._region, DEFAULTS[volume_type]
        storage = self._lookup(
            _query(("volumeType", "General Purpose"), ("volumeApiName", volume_type), ("regionCode", r)),
            "GB-Mo", d.storage_gb_month)
        iops, throughput = d.iops_month, d.throughput_mibps_month
        if volume_type == "gp3":
            iops = self._lookup(
                _query(("volumeApiName", "gp3"), ("group", "EBS IOPS"), ("regionCode", r)),
                "IOPS-Mo", d.iops_month)
            throughput = self._lookup(
                _query(("volumeApiName", "gp3"), ("group", "EBS Throughput"), ("regionCode", r)),
                "GiBps-mo", d.throughput_mibps_month, divisor=1024)
        prices = EbsPrices(storage, iops, throughput, d.free_iops, d.free_throughput_mibps)
        self._ebs[volume_type] = prices
        return prices

    def snapshot_gb_month(self) -> float:
        if self._snapshot is None:
            self._snapshot = self._lookup(
                _query(("productFamily", "Storage Snapshot"), ("regionCode", self._region)),
                "GB-Mo", SNAPSHOT_DEFAULT_GB_MONTH, highest=True)
        return self._snapshot

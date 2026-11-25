# Savings arithmetic and report fields

Read this at stage 4 of `guided.md`, before calculating any saving. It uses the public prices `gp2Public` and `gp3Public` from `pricing.md` and holds the finder's savings formula, percentage strings and report fields.

The actual source savings calculation is:

```ts
const currentAnnualCost = resource.annualCost;
const gp3TargetCost = currentAnnualCost * (gp3Public.finalPrice / gp2Public.finalPrice);
item.annualSavings = currentAnnualCost - gp3TargetCost;
```

Do not subtract the public-price difference directly from a discounted annual cost. `publicAnnualCost` supplies a separate report ratio: when both annual cost inputs are positive finite numbers, `Math.min(annualCost / publicAnnualCost, 1)`; otherwise `1`. **Do not apply this ratio a second time** to the target cost. Do not repeat the code comment about RIs/Savings Plans as a claim that they cover EBS.

Keep supplied annual costs unchanged. If billed annual cost is unavailable, identify that gap and apply the billing-failure rule in `billing.md`; do not populate a billed estimate. Only a public-price fallback that the customer allowed changes this (see `assessment-guide.md`). A public-price-only illustration may explicitly use `annualCost=publicAnnualCost=gp2Public.finalPrice`; this is a disclosed input substitution by the harness, not a CUR measurement or a new finder rule. Label the resulting estimate accordingly. Do not quietly replace a supplied cost with this illustration.

Preserve full precision until display and validation. Apply the two savings checks from the table in `checks.md`. Annotate non-finite savings as `annual-savings-not-finite` and non-finite annual cost as `annual-cost-not-finite`, as the source does; missing or zero-denominator inputs do not justify a meaningful saving.

When the savings calculation runs, retain the public-price saving as its own amount as well as both percentage strings. These assignments are copied from the source:

```ts
item.finderReportData.savingsPercentage =
  currentAnnualCost > 0 ? ((item.annualSavings / currentAnnualCost) * 100).toFixed(1) : '0.0';
item.finderReportData.publicSavings = gp2Public.finalPrice - gp3Public.finalPrice;
item.finderReportData.publicSavingsPercentage =
  gp2Public.finalPrice > 0
    ? (((gp2Public.finalPrice - gp3Public.finalPrice) / gp2Public.finalPrice) * 100).toFixed(1)
    : '0.0';
```

For machine comparison, keep `finderReportData={}` until parameters are populated. Parameter population sets `resourceFixedConfiguration={retypeToClass:"gp3"}`, `volumeId`, `volumeSize`, and all six sizing values. When the cost calculation runs, fill these exact report fields using the formulas above:

```text
annualSavings = currentAnnualCost - gp3TargetCost
gp2Cost = currentAnnualCost; gp3Cost = gp3TargetCost
gp2PublicCost = gp2Public.finalPrice; gp3PublicCost = gp3Public.finalPrice
calculatedGp2Cost = gp2Public.finalPrice; calculatedGp3Cost = gp3Public.finalPrice
gp2StorageCost = gp2Public.storageCost; gp3StorageCost = gp3Public.storageCost
gp3IopsCost = gp3Public.iopsCost; gp3ThroughputCost = gp3Public.throughputCost
hourlyGp2Cost = gp2Public.finalPrice / (365 * 24)
hourlyGp3Cost = gp3Public.finalPrice / (365 * 24)
```

Include the two percentage strings, publicSavings and discountRatio already defined. `gp2StorageRate`/`gp3StorageRate` are `"$" + parseFloat((storageCost / size / 12).toFixed(6))`. `gp3IopsRate` is `"$" + parseFloat((iopsCost / iopsValue / 12).toFixed(6))` when `iopsValue > 0`, otherwise `"$0.00"`; throughput uses the same expression with throughputCost/throughputValue. These describe the source report, not additional pricing assumptions. Retain this calculated report data and fixed-configuration record on a later savings rejection, but omit actionable fixer parameters. The actionable projection on approval is `{resourceId,gp3Iops,gp3Throughput}`; platform monitoring IDs/HTML are outside this skill's algorithm comparison.

Thus zero current annual cost produces the display string `'0.0'`, not an undefined percentage. A resource rejected before calculation has no calculated percentage; do not populate one from this fallback. Per-unit rates use `.toFixed(6)` then `parseFloat`; rendered USD money has two decimals; hourly public cost is annual public cost divided by `365 * 24`. Do not round before threshold comparisons.

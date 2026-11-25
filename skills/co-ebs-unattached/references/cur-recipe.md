# Existing CUR recipe (`billing` mode with a supplied CUR table)

Read this in stage 2 of `guided.md` only when the customer supplied a CUR table for a `billing` request (`economicsMode:"cur"`), or when a claim of native discovery coverage has to be checked. Without a supplied CUR table, billed costs come from Cost Explorer as `billing-cost-explorer.md` describes; never ask the customer to enable a CUR. It produces the `annualCost` that `costing.md` consumes.

Use the customer's known CUR table/view and matching schema; do not guess a database, payer or table. Record exact query, table/schema revision, query ID, UTC bounds, selected account/region/IDs, all result pages and returned cost values. For a historical replay, substitute its recorded evaluation time consistently for `NOW()` and `CURRENT_DATE`. For a live query freeze one evaluation instant and derive both from it. Do not widen scope.

1. Read the **31-day** usage-start window `[UTC midnight 32 days before asOf, UTC midnight one day before asOf)`. Require `line_item_product_code='AmazonEC2'`, resource ID `LIKE '%vol-%'`, nonempty `product_region`, and usage type matching any of `%VolumeUsage%`, `%VolumeP-IOPS%`, `%VolumeP-Throughput%`. Exclude Tax/Credit/Refund. For storage rows require `product_volume_api_name` in `gp2,gp3,io1,io2,standard,st1,sc1`; retain non-VolumeUsage rows regardless of that field. Add the requested account, region and ID predicates before aggregation.
2. Compute each row's amortized amount using this ordered CASE, preserving SQL NULL/coalesce semantics. Missing schema columns are a schema/evidence gap, not permission to replace this expression with unblended cost:

```sql
CASE
 WHEN line_item_line_item_type = 'SavingsPlanRecurringFee'
  AND date_trunc('month', line_item_usage_start_date) = date_trunc('month', CURRENT_DATE) THEN 0
 WHEN line_item_line_item_type = 'SavingsPlanRecurringFee' THEN
  (savings_plan_total_commitment_to_date - savings_plan_used_commitment)
  * (1 - discount_total_discount / line_item_unblended_cost)
 WHEN line_item_line_item_type = 'SavingsPlanCoveredUsage' THEN
  COALESCE(savings_plan_net_savings_plan_effective_cost, savings_plan_savings_plan_effective_cost)
 WHEN line_item_line_item_type IN ('SavingsPlanNegation', 'SavingsPlanUpfrontFee') THEN 0
 WHEN line_item_line_item_type = 'RIFee'
  AND date_trunc('month', line_item_usage_start_date) = date_trunc('month', CURRENT_DATE) THEN 0
 WHEN line_item_line_item_type = 'RIFee' THEN
  COALESCE(reservation_net_unused_amortized_upfront_fee_for_billing_period,
           reservation_unused_amortized_upfront_fee_for_billing_period)
  + COALESCE(reservation_net_unused_recurring_fee, reservation_unused_recurring_fee)
 WHEN line_item_line_item_type = 'DiscountedUsage' THEN
  COALESCE(reservation_net_effective_cost, reservation_effective_cost)
 WHEN line_item_line_item_type = 'Fee' AND reservation_reservation_a_r_n <> '' THEN 0
 WHEN line_item_line_item_type IN ('Tax', 'Refund', 'Credit') THEN 0
 ELSE COALESCE(line_item_net_unblended_cost, line_item_unblended_cost)
END
```

3. Group by usage account, region and resource ID. `annualCost=ROUND(SUM(amortized amount)/31*365,2)`. Native `publicAnnualCost=ROUND(SUM(pricing_public_on_demand_cost)/31*365,2)` and separate storage/IOPS/throughput amounts use the same formula with nonmatching usage types contributing zero; this finder does not use those component amounts or publicAnnualCost. Resource name is `MAX(COALESCE(resource_tags_user_cloudfix_name,resource_tags_user_name,''))`.
4. Native discovery additionally requires `MAX(line_item_usage_end_date) > asOf - 7 days`, annualCost **strictly greater** than the actual tenant `resourceCostThreshold`, and `MAX(resource_tags_user_cloudfix_dont_fix_it)` null/empty. Apply those only when claiming native discovery coverage, with the actual tenant threshold and configured account/region scope. The pipeline chooses a finder override when present, otherwise the tenant threshold; this finder has no override. The database default is 100, but it is not evidence that this tenant uses 100. The standalone assessment does not invent that discovery threshold. If those settings/tag history are unavailable, mark discovery parity unverified. A selected resource absent from this query does not establish annualCost zero. Retain a rejected discovery row/reason separately from validator results.

A CUR-derived figure is the source's historical annualization, not a forecast or realized saving. Differences in CUR freshness, discounts, source revision, configuration or observation time can produce different CloudFix totals.

In `assessment.json` the 31-day window is each row's `costs.source_period`, with `start` and the exclusive `end` as UTC timestamps, and `costs.basis` is `resource_billed`. Save the query, its ID, every result page and any failure in `evidence/cur-query.json`.

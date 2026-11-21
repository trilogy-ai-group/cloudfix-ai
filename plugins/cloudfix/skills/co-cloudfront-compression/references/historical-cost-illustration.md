# Historical cost illustration and requested economics

Read this only when the customer asks for the historical cost illustration, for savings priced from billing (CUR) or for an assessment of supplied resource cost facts. A technical assessment never needs it. "Native" and "the source" mean the CloudFix finder whose rules this skill reproduces.

Nothing here produces an established saving. The finder's own savings output is zero because savings tracking is disabled; the 65% figure is a model over one billing week. Both go in `details.historical_cur_illustration`, never in `costs.annual_savings`, `first_year_savings` or `totals`. `references/assessment-guide.md` ("Pricing modes and costs") fixes the field names. Save the query evidence as `evidence/cur-query.json` and the arithmetic as `evidence/calculations.json`, keyed by distribution ID.

## Modes

- **Supplied resource:** trust caller-supplied `resourceId`, `awsAccount`, `awsRegion`, `resourceName`, `annualCost`, `publicAnnualCost`, `annualSavings`, `dataTransferred` with their currency, period and provenance. These are input-owned facts. Do not recompute an already supplied estimate from rounded annual cost or overwrite it to force 65%. Explain any supplied qualifications.
- **CUR:** derive those resource fields from existing customer resource-level billing evidence using the exact rules below. Use the authorized query tools, workgroup and output location already available. Athena scans may incur charges; use the user's applicable authorization. Save exact SQL/table mapping, query ID, reported scan size/cost, every result page and errors. Do not create a billing export or query infrastructure.
- **Incomplete economics:** evaluate supported technical checks, leave missing financial facts unknown, and identify what is needed for a complete assessment. Do not substitute account-wide Cost Explorer charges, public pricing, bytes times a guessed rate, or a remembered discount.

Every request keeps `scope.pricing_mode:"technical_only"`; the requested mode (`billing` for CUR, or `supplied`) goes in `run_metadata.economics_mode`. An otherwise eligible distribution stays `recommended`: this finder estimates no savings, so a price request never turns it into `needs_evidence`.

## The two savings values

**The source has two distinct savings values.** The CUR parser stores its 65% estimate in **`resource.annualSavings`**. The base validator initializes **`native_equivalent_projection.annualSavings` to `0`**, and this finder never copies the input estimate into it. Preserve both. A technically approved distribution can therefore have a positive CUR estimate and **zero native final savings**. Report the latter as the source's output, not proof of no physical saving; report the former as an unverified model estimate, not measured or native-final savings. Do not copy the historical estimate into final savings or characterize zero as a defect. Actual cost reduction depends on traffic compressibility, requests/content already compressed, cache behavior and the customer's pricing plan; this skill does not measure those factors.

## CUR query

For CUR, freeze the query's `CURRENT_DATE` to the UTC date of `asOf` for replay. Map a verified existing export to these native columns; inaccessible/unknown columns remain evidence gaps. If a net-cost or optional name-tag column is proven absent, it may be projected as NULL with that limitation recorded. No resource-level ID means no supported per-distribution allocation. Use the same currency basis for summed monetary fields.

Select rows with all three source predicates:

```text
line_item_product_code = 'AmazonCloudFront'
product_product_family = 'Data Transfer'
line_item_usage_start_date >= date_trunc('week', CURRENT_DATE - interval '8' day)
line_item_usage_start_date <  date_trunc('week', CURRENT_DATE - interval '1' day)
```

`date_trunc('week',...)` starts Monday at 00:00 UTC. This is not a rolling seven-day window. On 2026-11-17 it is `[2026-11-09,2026-11-16)`; on Monday 2026-11-16 it is `[2026-11-02,2026-11-09)`. Execute the boundary calculation and keep the actual start/end. Add verified account/selected-resource scope filters to honor the request, recording narrowed discovery. Do not invent usage-type, transfer-direction, line-item-type or compression-percentage filters absent from the source.

Group by `split_part(line_item_resource_id,'distribution/',2)` and `line_item_usage_account_id`. The first becomes `resource_id`; the latter `account_id`. For each group, `region=MAX(product_region)` and `resource_name=MAX(COALESCE(resource_tags_user_cloudfix_name,resource_tags_user_name,''))`. Using SQL null/coalesce/sum semantics, calculate:

```text
C = SUM(COALESCE(line_item_net_unblended_cost,line_item_unblended_cost))
P = SUM(pricing_public_on_demand_cost)
U = SUM(line_item_usage_amount)
annualized_amortized_cost = ROUND(C / 7 * 365, 2)
annualized_public_cost    = ROUND(P / 7 * 365, 2)
estimated_savings         = ROUND(0.65 * C / 7 * 365, 2)
data_transferred         = U * 4.36
```

SQL ROUND here rounds to two decimal places, with half values away from zero; retain the unrounded expressions. Net cost `0` is present and must not fall back to unblended cost. Calculate `estimated_savings` independently of the rounded annual cost. Example: `C=0.03` gives annual cost `1.56` and estimate `1.02`; 65% of rounded `1.56` would incorrectly give `1.01`. The factor `4.36` is a weekly-to-monthly usage projection; preserve the billing usage units, do not assume GB. The model applies 65% to all selected Data Transfer cost, without measuring compressibility, traffic shares or effective savings under a pricing plan. Partial billing coverage still divides by seven; disclose that limit rather than repairing the formula.

## CUR result

Select groups with **`annualized_amortized_cost > resourceCostThreshold`**, strictly; sort descending. For native-discovery comparison this is the supplied tenant threshold described in stage 1 of `SKILL.md`, not a catalogue or invented fixed threshold. Map the two annualized fields to `annualCost`/`publicAnnualCost`, `estimated_savings` to resource `annualSavings`, `data_transferred` to `dataTransferred`, and identity fields to the camel-case resource fields of the supplied-resource mode above. Native parsing uses unary numeric conversion (`+`), not additional rounding. Preserve raw missing/malformed values and any resulting zero/nonfinite projection as a data-quality issue; never present a coerced missing input as a measured zero.

The native CUR base also keeps only regions in its environment's `AVAILABLE_REGIONS` plus literal `global`; its default is `us-east-1` plus `global`. Blank region is not automatically global. Unknown platform region configuration stays unknown; apply the user's scope without claiming identical catalogue discovery. Complete explicit resource input does not require reproducing the platform's discovery gate.

## Rule 4 and the financial summary

Once `annualCost` is established, evaluate `MaximumSavings` as `references/checks.md` defines it and record its result in `checks`.

Record per resource, in `details.historical_cur_illustration`: justified current annual cost, separately named `source_cur_estimated_annual_savings` (resource.annualSavings) and `native_final_annual_savings` (zero), cost basis/currency/window, public cost and monthly projected usage when supplied, and complete expressions/provenance.

Missing source estimate cannot be reconstructed exactly from rounded annual cost. If requested economics lack the source estimate or annual cost, financial assessment is incomplete even when technical eligibility is known. In technical-only mode a missing estimate is not a blocker or an invented financial pass. Preserve missing context fields as unknown. There is no native new-cost result; an optional arithmetic illustration must identify which savings value it subtracts.

## Totals

Report technical candidate counts independently of billing. Sum monetary amounts only for compatible fully assessed eligible resources; keep native-final totals and modeled CUR-estimate totals separately named and identify every excluded count/amount. Label native zero as disabled savings tracking and historical 65% totals as unverified illustrations, never an opportunity value or established saving. Missing required financial evidence or framework history prevents a native-complete total. A separate standalone subtotal may include technically eligible resources with supported economics and unknown platform history, but must carry that qualification and `framework_context_complete:false`; never label it fully assessed/native-complete. Preserve the resource's standalone eligibility and supplied amounts. Never call either total realized savings.

In `assessment.json` these separately named totals are the top-level `illustration_totals` object; `totals.annual_savings` stays null. A standalone run reports unknown platform history as a qualification beside `RecommendationFinalized: not_evaluated`.

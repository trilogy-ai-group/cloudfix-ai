# Billing mode: effective rates from Cost Explorer

Read this in stage 2 of `SKILL.md` when the pricing mode is `billing` and the customer did not supply a CUR export (`cost-explorer`), before any Cost Explorer call. It gives the two exact requests, their validation, the rate arithmetic, how each bucket's economic row is built, the failure rule, the rule for a conditional request, and the qualifications. `references/assessment-guide.md` says how the result is saved.

**What this method is.** Cost Explorer reports what the account was billed for S3 storage in one region, by usage type. It does not say which bucket a charge belongs to. This method therefore derives one **account-and-region effective rate** per storage class, in USD per GB-month as billed, and applies it to each bucket's **7-day mean size** from the billed-size series of `references/collection.md`. The result is a billed rate applied to a measured size. It is not that bucket's bill, and two buckets of the same class always get the same rate. Only the price inputs differ from `references/public-price-model.md`: the checks, thresholds, the `0.65` and `0.4185` coefficients, the fee constants and the savings formulas are unchanged.

If complete recorded Cost Explorer responses are supplied, process them without new calls; a supplied rate without its complete supporting responses and matching scope is not usable. Otherwise finish and save the `references/collection.md` reads first. Make no Cost Explorer call when the completed collection holds no bucket in scope with Standard or Standard-IA bytes: no row could use a rate. In this method a bucket holds a class when that class's first-value size or its 7-day mean from `references/collection.md` is above 0. Use existing credentials only, either the assessing identity or a separate existing billing profile, and verify their identity with `sts:GetCallerIdentity`; a payer account can differ from the assessed account. Both operations are called at the Cost Explorer endpoint in `us-east-1` and need `ce:GetDimensionValues` and `ce:GetCostAndUsage`. AWS charges for every Cost Explorer API request: disclose about 0.02 USD for the two unpaginated requests, with more for pages, retries and further regions. Do not create a permission, enable Cost Explorer or any billing setting, or widen the scope to the payer's other accounts.

1. **Window.** Freeze UTC midnight of the billing clock. `Start = midnight − 32 days` and `End = midnight − 1 day`, as ISO dates. End is exclusive, so the window is 31 days. A retry of a saved snapshot keeps its resource observation time and records the new billing clock and window.
2. **Common filter.** Every request carries `Filter.And` of these four dimensions, each shaped `{"Dimensions":{"Key":"<key>","Values":["<value>"]}}`: `LINKED_ACCOUNT=[the assessed account]`, `REGION=[the buckets' region]`, `SERVICE=["Amazon Simple Storage Service"]`, `RECORD_TYPE=["Usage"]`. For several regions, repeat steps 3 to 8 per region.
3. **Read the usage types.** Call `GetDimensionValues` with `TimePeriod={Start,End}`, `Dimension="USAGE_TYPE"`, `Context="COST_AND_USAGE"`, `SearchString="TimedStorage"` and the common filter. Read every page through `NextPageToken`; an absent token ends paging, and an empty, repeated or non-string token is an invalid response. Save the complete returned list. Map a usage type to a storage class only from the names actually returned, with these anchored patterns:

   | Class this skill prices | Usage type pattern |
   | --- | --- |
   | Standard payload storage | `^(?:[A-Z0-9]+-)?TimedStorage-ByteHrs$` |
   | Standard-IA payload storage | `^(?:[A-Z0-9]+-)?TimedStorage-SIA-ByteHrs$` |

   The optional prefix is AWS's billing code for the region, such as `USW2-`; some regions have none. Record every other returned usage type with the reason it was left out: One Zone-IA, Intelligent-Tiering, the Glacier classes, small-object overhead (`SmObjects`) and any name you do not recognize. Never map a class by resemblance or from memory, and never add a usage type that was not returned. Usually one usage type matches per class; if several do, keep them all and add their amounts.
4. **Read cost and usage.** Call `GetCostAndUsage` once with the same `TimePeriod`, `Granularity="DAILY"`, `Metrics=["NetUnblendedCost","UnblendedCost","UsageQuantity"]`, `GroupBy=[{"Type":"DIMENSION","Key":"USAGE_TYPE"}]`, and the common filter plus `USAGE_TYPE=[every retained value of both classes]`. Read every page through `NextPageToken` with the token rule of step 3, and keep the complete requests and responses. If step 3 retained no usage type for either class, make no `GetCostAndUsage` call: both classes have no usable usage.
5. **Validate before any arithmetic.**
   - Each result covers one day inside the window, days are unique and do not overlap, and `Estimated` is a boolean.
   - Each group's `Keys` is exactly one retained usage type, at most once per day. A day without a group for a class is a day without recorded usage of that class; count it, never fill it in.
   - Amounts are finite decimal strings and cost units are `USD`.
   - **Unit.** `UsageQuantity` must be in GB-months: its `Unit` is `GB-Mo`, `GB-Month` or `GB-Months`, compared case-insensitively. Where the group's unit is absent or `N/A`, the unit that `GetDimensionValues` returned for that usage type (`Attributes.unit`) must be one of those instead. If both are present they must agree. A quantity whose unit cannot be verified this way, including a byte-hour unit, is invalid data; do not convert it by assumption.
   - Use `NetUnblendedCost`. Fall back to `UnblendedCost` **only** where the net metric is absent or null, or its `Amount` is null, and count every fallback. An empty, malformed or non-numeric net amount is an error, not permission to fall back.
   - Quantity is never negative, and a zero quantity requires zero cost.
6. **Derive the rates.** With the calculator, at full precision, for each class:

   ```text
   selectedCost[class]  = sum over days and that class's usage types of NetUnblendedCost, or the permitted UnblendedCost fallback
   usageGbMonths[class] = sum of the same groups' UsageQuantity
   rate[class]          = selectedCost[class] / usageGbMonths[class]      USD per GB-month
   ```

   Require a positive finite `usageGbMonths`, a finite non-negative `selectedCost` and a finite non-negative `rate`. The quantity is already in GB-months, so the quotient is the monthly rate for one GB: do not multiply or divide by days, and do not annualize by `365/31`. Missing days are allowed when the returned and requested day counts are recorded; never invent a period. A class with no positive usage is the "no usable usage" case below; any other failed requirement here is invalid data, the "denied, failed or invalid" case.
7. **Build each bucket's economic row.** Open `references/public-price-model.md` only for its `// public-s3-storage-calculation` block and the field list in its step 5; make no catalog read. Copy `s3PublicStorageModel` byte for byte and call it with the bucket's 7-day mean sizes from the billed-size series of `references/collection.md`, `S=standardGbBilled` and `A=standardIaGbBilled`, and one flat band per class. The first-value `standardGb` and `standardIaGb` stay the inputs of orders 9 to 12, as in the native finder, which prices a seven-day billing window and measures object fees on today's bytes:

   ```text
   standardDimensions = [{begin: 0, end: "Inf", rate: rate.standard}]
   siaDimensions      = [{begin: 0, end: "Inf", rate: rate.standardIa}]
   model = s3PublicStorageModel(S, A, standardDimensions, siaDimensions)
   C = model.annualIncludedStorageCost      G = model.grossSavings
   ```

   **Tiered prices.** Public Standard pricing falls in bands as volume grows, and AWS applies those bands to the organization's combined regional usage, which one bucket's size cannot reproduce. The effective rate is already the average of the bands and discounts the account's storage of that class was billed at. It therefore replaces the whole band table with one band from zero to infinity, and no bucket is banded on its own. Each bucket gets the account's average, even where its own marginal GB is billed in a cheaper or dearer band. Set `tierAllocation:"account-and-region effective rate, one flat band per class"`.

   The row is the modeled row of step 5 in `references/public-price-model.md`, with these values: `annualCost=dataCosts=C`, `totalSavings=dataSavings=G`, `totalSavingsPercentage=dataSavingsPercentage=model.modelSavingFraction`, `stdGbMonths=S`, `iaGbMonths=A`, `gbMonths=S+A`, `girGbMonths=0`, `extraSavings=0`, `intMonitoringCosts=0`, `sizeBasis:"7-day mean of daily CloudWatch BucketSizeBytes"`, `sizeDays:{"standard":<returned days>,"standardIa":<returned days>}`, `basis:"cost-explorer-effective-rate"`, `costScope:"Standard+Standard-IA payload storage"`, `nativeCurEquivalent:false`. `publicAnnualCost` is null: this method reads no public price, and the effective-rate cost is not a public cost. The two zero credits mean what they mean in that step: no retrieval or early-delete saving is credited and no existing-monitoring credit is assumed, because Cost Explorer does not attribute those charges to a bucket.

   A class the bucket holds no bytes of (its first-value size and its 7-day mean are both 0) contributes nothing. A class with a first-value size above 0 always has a 7-day mean above 0 here, because `references/collection.md` makes the bucket `needs_evidence` otherwise. If that class has no rate, pass `rate: 0` for it solely to satisfy the function's argument and record `rateNotNeeded` for the row. Never do this for a class the bucket holds bytes of. If S+A is zero the model is undefined, as in the public-price model.
8. **Run the unchanged checks.** Feed `grossSavings=G`, the unchanged metric counts and bytes and the zero-credit assumption into `references/costing.md`'s copied function, then complete orders 9 to 12 exactly as written. Save `modeledResource` and `modeledCalculation` per bucket as the public-price model does. Save one `cost_explorer_effective_rate` record in `evidence/economics.json`: the billing identity, the window, both requests and every page, the full returned usage-type list with each mapping or exclusion reason, the verified units, and per class `{usageTypes, selectedCost, unblendedCost, usageGbMonths, rate, netFallbackGroups, daysWithUsage}`, with the days requested, returned and missing and the count of `Estimated` days.

**When Cost Explorer cannot produce a rate.** Never substitute a public price, a remembered rate or the other class's rate, and never write a saving for an affected row. Keep `scope.pricing_mode: billing`, retain all resource and billing evidence, and preserve the failure across a replay.

- **Denied, failed or invalid.** Either request is denied (for example `AccessDeniedException`, or Cost Explorer not enabled for the account), fails after retries, or fails validation in step 5. No rate exists. Orders 1 to 8 still run for every bucket. Every bucket that no completed check rejects is `error` with `costs: {"status": "not_calculated"}`. One `read_error` issue at stage `collection`, without a `resource_id`, carries the exact error and `request: {"kind": "aws_read", "name": "<the failed operation>", "service": "ce", "region": "us-east-1"}`.
- **No usable usage.** The reads succeeded, but a class has no retained usage type or no positive usage in the window. That class has no rate. A bucket holding bytes of it has no economic row and is `needs_evidence`, covered by one `missing_evidence` issue that names the class and what was returned, with `request: {"kind": "input", "name": "economics"}`. A bucket that needs only the other class is priced normally.

In both cases the run is `partial`, and a bucket that a completed check rejected stays `not_recommended`.

**Conditional request.** Public prices may replace billed costs only when the customer's own request allows it, in words such as "use billed costs if possible, otherwise public prices". A plain billed-cost request, an unstated basis or your own judgment is not that permission. With it:

1. Try Cost Explorer exactly as above.
2. If it produces every rate the buckets need, the result is the ordinary `billing` result.
3. If it cannot produce every rate the buckets need, say exactly why: the operation, error code and message, or which usage types were missing. Then run `references/public-price-model.md` for the whole scope. Save that assessment as `scope.pricing_mode: public_prices` with `costs.basis: public_price`, and label it public prices everywhere. Its first top-level qualification states that billed costs were requested first, which rate Cost Explorer could not produce and why, and that public prices were used as the customer allowed. One `issues` row of kind `qualification` carries the same reason, the Cost Explorer evidence reference and the `request` that would make billed costs possible. One class's rate without the other's, when a bucket needs the missing one, is this case too. Never mix two price sources in one file or one total.

**Saved result.** `scope.pricing_mode` is `billing`. Every priced row has `costs.basis: account_effective_rate`, `costs.estimation_method: model_estimate` because the tier mix is still modeled, `currency: USD`, and `costs.source_period: {"start": "<Start>T00:00:00Z", "end": "<End>T00:00:00Z"}`. The top-level `evidence_refs` include `evidence/economics.json#/cost_explorer_effective_rate`.

State these assumptions once, in the top-level `qualifications`, with the actual values:

- The current cost is an account-and-region effective rate for Standard and for Standard-IA storage (net billed cost divided by billed GB-months over the stated window), applied to each bucket's 7-day mean CloudWatch size, the window the CUR annualizes from, and projected for 12 months at that rate; today's size can differ. It is not that bucket's bill and not CUR-equivalent.
- The rate averages the pricing bands and discounts across all of the account's storage of that class in the region. A bucket's own band, lifetime, size changes and adjustments are not reconstructed.
- Payload storage only. Requests, retrievals, early deletes, Standard-IA minimum-size and minimum-duration charges and every other storage class are excluded. No retrieval or early-delete saving and no existing-monitoring credit is assumed, so buckets that already hold Intelligent-Tiering objects can have their fees overstated.
- Visible net amounts do not prove that every payer discount is visible, and the billing view may be pro forma.
- Every `UnblendedCost` fallback, the missing-day count and any `Estimated` day.
- Each bucket priced on fewer than 7 billed-size days, with its count.

The limits in step 6 of `references/public-price-model.md` that concern excluded charges and object counts apply here too. Neither this estimate nor a proposed change proves a realized saving.

**Resource-level Cost Explorer data, optional.** Some customers have already turned on daily resource-level data for S3 in Cost Explorer. Never require it, never probe for it and never ask anyone to enable it. Use it only when the customer says it is already on, or supplies its records, and wants bucket-level billed cost. The operation is then `GetCostAndUsageWithResources` with the same filter, metrics, class patterns and validation, grouped by `RESOURCE_ID` and `USAGE_TYPE`, inside the short lookback that operation retains. Read what it actually returns; if it rejects the request or returns no bucket keys, the data is not usable. Accept the records only when they prove bucket identity, account and region, period, usage classes and coverage for every bucket to be priced. Each bucket's own rate (its cost divided by its GB-months) then replaces the account rate in step 7, and the rows are `resource_billed` with that stated. Otherwise use the account-and-region rate for every bucket: one assessment has one basis.

## Worked example

Synthetic numbers, not AWS prices. Thirty-one daily results in `us-east-1` add up to the class totals below; the bucket holds 4,000 GB of Standard and 1,000 GB of Standard-IA in 2,000,000 objects, with no archived or Intelligent-Tiering objects, and its 7-day mean sizes equal today's because it is stable.

```json
{
  "usageTypesReturned": ["TimedStorage-ByteHrs", "TimedStorage-SIA-ByteHrs", "TimedStorage-SIA-SmObjects", "TimedStorage-ZIA-ByteHrs", "TimedStorage-INT-FA-ByteHrs"],
  "retained": {"standard": ["TimedStorage-ByteHrs"], "standardIa": ["TimedStorage-SIA-ByteHrs"]},
  "classes": {
    "standard": {"selectedCost": 218.24, "usageGbMonths": 9920, "rate": 0.022},
    "standardIa": {"selectedCost": 19.84, "usageGbMonths": 1984, "rate": 0.01}
  },
  "bucket": {"standardGb": 4000, "standardIaGb": 1000, "standardGbBilled": 4000, "standardIaGbBilled": 1000, "sizeDays": {"standard": 7, "standardIa": 7}, "intelligentTieringGb": 0, "objectCount": 2000000, "archivedObjectCount": 0},
  "model": {"annualIncludedStorageCost": 1176, "modelSavingFraction": 0.55, "grossSavings": 646.8},
  "estimate": {"monitoringFee": 60, "smallObjectShare": 0.048828125, "netSavings": 586.8, "transitionCharge": 20, "firstYearNetSavings": 566.8},
  "costs": {"status": "calculated", "basis": "account_effective_rate", "estimation_method": "model_estimate", "currency": "USD", "current_annual": 1176, "target_annual": 589.2, "annual_savings": 586.8, "one_time_cost": 20, "first_year_savings": 566.8}
}
```

Here `rate.standard = 218.24 / 9920`, `C = 12 * (4000 * 0.022 + 1000 * 0.01)`, the fraction is `1 - 0.4185 / (0.8 + 0.65 * 0.2)` and `G = C * 0.55`. The fee function then gives the net figure, and orders 10 to 12 pass (`566.8 > 0`, `586.8 > 50`, `586.8 <= 1176`). Amounts are shown rounded; keep the calculator's unrounded results in a real run.

# Public-price mode: the storage model

Read this in stage 2 of `SKILL.md` only when the pricing mode is `public_prices` (`public-price-model`); Cost Explorer `billing` opens it only for the block and step 5 field list that `references/billing-cost-explorer.md` names. It gives the catalog read, the product selection, the storage model function and how its result becomes the economic row for `references/costing.md`. Save the records it names (`modeledResource`, `modeledCalculation`, `public_price_model`) in the evidence folder; `references/assessment-guide.md` says how the row's `checks`, `costs` and `details` are filled from them. In Cost Explorer `billing`, `references/billing-cost-explorer.md` sends you here for the function in step 4 and the field list in step 5 only; steps 1 and 2 and the public-price labels do not apply there.

This option needs no CUR. Reuse `references/collection.md`'s exact CloudWatch collection/reduction, the lifecycle checks and `references/costing.md`'s unchanged fee function. It models only **Standard + Standard-IA payload storage**, with `nativeCurEquivalent:false`. It does not establish the current full bucket bill, measured future access or actual savings. Preserve a supplied native economic row unchanged; any requested comparison is a separate `modeledResource`, never replacement input. The storage model below is this mode's economic input row, computed once the `references/collection.md` tier sizes are collected; `references/costing.md` applies the fees to it.

1. **Read the regional public catalog** at `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/<bucket-region>/index.json`. This is a public HTTPS read; no Pricing IAM action or new billing feature is required. Retain the complete raw catalog privately or a verified complete local reference, URL, retrieval time, version/publication date and hash. Do not claim today's catalog describes a historical replay.
2. **Resolve Standard and Standard-IA storage.** Select `productFamily=Storage`, `servicecode=AmazonS3`, exact `regionCode`, `operation=""`, and `volumeType=Standard` or `Standard - Infrequent Access`. Verify the corresponding usage type ends in `TimedStorage-ByteHrs` or `TimedStorage-SIA-ByteHrs`. Require one compatible SKU/OnDemand term per applicable class with recorded effective date, every price dimension/rate code, USD currency and `GB-Mo` unit. Preserve all raw attributes. Missing/ambiguous products, overlapping terms, unusable rates/units or incomplete dimensions leave economics unavailable; never use remembered regional prices. Dimension ranges must start at zero, be contiguous and nonoverlapping, and cover the modeled quantity, with the complete last band ending at infinity. Identical repeated records can be deduplicated while retaining their originals; conflicts cannot.
3. **Use the collected payload sizes.** `S=standardGb`, `A=standardIaGb`, after the source's bytes/1024³ conversion and per-tier 0.01-GB rounding. Do not include One Zone-IA, GIR, Glacier, Deep Archive or existing IT storage in this base. Use the native object-count/archived-overhead reductions and all seven tier measurements for coverage. Missing/failed/partial reads remain missing even if a native normalization produces zero.
4. **Compute the storage model.** For each class use progressive bands, not one marginal price for all bytes. `tierAllocation="bucket-alone progressive public quote"`: account/organization-wide storage aggregation, discounts and payer rates are unknown. Execute this function using validated dimension ranges; `end` is a number or the catalog string `Inf`.

```javascript
// public-s3-storage-calculation
function s3MonthlyStorageCost(sizeGb, dimensions) {
  if (!Number.isFinite(sizeGb) || sizeGb < 0 || !Array.isArray(dimensions) || dimensions.length === 0)
    throw new Error('Missing or invalid storage quantity/dimensions');
  const numeric = value => (typeof value === 'number' || (typeof value === 'string' && value.trim() !== '')) ? Number(value) : NaN;
  const bands = dimensions.map(d => ({begin: numeric(d.begin), end: d.end === 'Inf' ? Infinity : numeric(d.end), rate: numeric(d.rate)}))
    .sort((a, b) => a.begin - b.begin);
  let boundary = 0, cost = 0;
  for (const band of bands) {
    if (!Number.isFinite(band.begin) || band.begin !== boundary || !(band.end > band.begin)
        || !(Number.isFinite(band.end) || band.end === Infinity) || !Number.isFinite(band.rate) || band.rate < 0)
      throw new Error('Missing, overlapping or invalid pricing range');
    cost += Math.max(0, Math.min(sizeGb, band.end) - band.begin) * band.rate;
    boundary = band.end;
  }
  if (boundary !== Infinity) throw new Error('Incomplete pricing ranges');
  return cost;
}
function s3PublicStorageModel(standardGb, standardIaGb, standardDimensions, siaDimensions) {
  const total = standardGb + standardIaGb;
  if (![standardGb, standardIaGb].every(x => Number.isFinite(x) && x >= 0) || !(total > 0))
    throw new Error('No supported transitionable payload');
  const annualIncludedStorageCost = 12 * (s3MonthlyStorageCost(standardGb, standardDimensions)
    + s3MonthlyStorageCost(standardIaGb, siaDimensions));
  const modelSavingFraction = 1 - 0.4185 / (standardGb / total + 0.65 * standardIaGb / total);
  return {annualIncludedStorageCost, modelSavingFraction, grossSavings: annualIncludedStorageCost * modelSavingFraction};
}
```

The `0.65` and `0.4185` coefficients are the original model, **not current relative prices**. Do not replace them with a live Standard-IA/Standard price ratio or a new access forecast. No intermediate rounding is introduced in this extension. If S+A is zero, leave the model undefined; no synthetic zero-savings recommendation.

5. **Feed the same fee function.** Save a separate `modeledResource` with `basis:"public-price-model"`, `costScope:"Standard+Standard-IA payload storage"`, `nativeCurEquivalent:false` and `tierAllocation` above. Set its calculation inputs `annualCost=publicAnnualCost=dataCosts=C` (the included-storage annual model), `totalSavings=dataSavings=G`, `totalSavingsPercentage=dataSavingsPercentage=modelSavingFraction`, `stdGbMonths=S`, `iaGbMonths=A`, `gbMonths=S+A`. These are current metric-derived quantities, **not CUR GB-month measurements**. Use `girGbMonths=0` solely to indicate no GIR bytes in this model; retain actual measured GIR bytes separately. No field should imply a whole-bucket bill.

Use `extraSavings=0` only as **no unmeasured retrieval/early-delete savings credited**, and `intMonitoringCosts=0` only as **no existing-monitoring credit assumed**. Actual charges remain unknown. Preserve independently supplied native values untouched; do not silently blend actual inputs into this model. Because existing IT bytes are still excluded but unknown monitoring credits are zero, the source candidate-count/fee bound can overcount existing IT objects. Carry that limitation into the result. Feed `grossSavings=G`, unchanged metric counts/bytes and the explicit zero-credit assumption into `references/costing.md`'s copied function. Apply the same coverage, first-year, minimum and maximum checks to this model, label them `modeled_checks`, and keep `nativeCalculation` unavailable unless real native economic inputs were supplied. `modeledCalculation` preserves all unrounded fields and check decisions.

6. **Explain the boundaries.** Public payload pricing excludes Standard-IA minimum billable object-size overhead and 30-day minimum-duration charges; `StandardIASizeOverhead` is a distinct metric not priced here. It also excludes requests, retrievals, early deletions, negotiated discounts, taxes, future-arrival transitions and unsupported classes. Mixed classes produce a scoped partial estimate with whole-bucket economics unassessed, not an extra native eligibility rejection. Native object counts can include GIR/One Zone-IA, small IT objects, delete markers and multipart parts; the copied fee calculation does not identify individual transitionable objects. The native CUR `_IA` wildcard includes One Zone-IA; this new public base deliberately does not treat it as supported transitionable SIA.

Save `public_price_model` with complete price/metric evidence, included/excluded bytes/classes, current-size steady-state assumption, fixed model coefficients/fees, every zero-credit assumption, component amounts and full qualifications. A read failure in this requested mode returns an economics error with resource evidence retained; it does not authorize CUR, CE or remembered-price fallback. Native/supplied calculations, public models and qualitative results have separate totals.

Official references: [S3 metrics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/metrics-dimensions.html), [S3 pricing](https://aws.amazon.com/s3/pricing/), [lifecycle transition constraints](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-transition-general-considerations.html), [CE daily resource data](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-resource-daily.html).

# Costing: metric coverage, object fees and savings

Read this in stage 4 of `SKILL.md`, for every row that reaches order 9. It defines the inputs, the fee function, the `HasObjectMetrics` coverage reasons, both value objects and which figure feeds orders 11–12. Copy the function into the calculator or script byte for byte. `resource` is the row's economic row (`references/economics-inputs.md`). Save inputs, expressions and unrounded results in the evidence folder.

Use the normalized values from `references/collection.md`. Let `N` be the latest object count (undefined becomes 0 for the formula), `A` archived count, `T=standardGb+standardIaGb`, `I=intelligentTieringGb`, `G=resource.totalSavings`, and `E=resource.intMonitoringCosts` if finite, otherwise 0. Preserve missing-input flags beside the fallback. Constants are source USD rates, **not live price lookups**.

Call this function with `{grossSavings:G,objectCount:N,archivedObjectCount:A,transitionableSizeGb:T,intelligentTieringSizeGb:I,existingMonitoringFee:E}`.

```javascript
const IT_MIN_OBJECT_SIZE_KB = 128;
const IT_MONITORING_FEE_PER_OBJECT_PER_YEAR = (0.0025 / 1000) * 12;
const IT_TRANSITION_FEE_PER_OBJECT = 0.01 / 1000;

function estimateNetSavingsAfterObjectFees(inputs) {
  const { grossSavings, objectCount, archivedObjectCount, transitionableSizeGb, existingMonitoringFee } = inputs;
  const monitoredObjectCount = Math.min(
    existingMonitoringFee / IT_MONITORING_FEE_PER_OBJECT_PER_YEAR,
    (inputs.intelligentTieringSizeGb * 1024 * 1024) / IT_MIN_OBJECT_SIZE_KB,
  );
  const candidateObjectCount = objectCount - archivedObjectCount - monitoredObjectCount;
  const averageObjectSizeKb =
    candidateObjectCount > 0 ? (transitionableSizeGb * 1024 * 1024) / candidateObjectCount : 0;
  const monitoringFee = Math.max(0, candidateObjectCount) * IT_MONITORING_FEE_PER_OBJECT_PER_YEAR;
  const smallObjectShare = averageObjectSizeKb > 0 ? Math.min(1, IT_MIN_OBJECT_SIZE_KB / averageObjectSizeKb) : 1;
  const netSavings = Math.min(grossSavings - monitoringFee, grossSavings * (1 - smallObjectShare));
  const transitionCharge = Math.max(0, candidateObjectCount) * IT_TRANSITION_FEE_PER_OBJECT;
  return {
    objectCount,
    transitionableSizeGb,
    monitoredObjectCount,
    candidateObjectCount,
    averageObjectSizeKb,
    existingMonitoringFee,
    monitoringFee,
    smallObjectShare,
    netSavings,
    transitionCharge,
    firstYearNetSavings: netSavings - transitionCharge,
  };
}
```

Do not round candidate count or any intermediate. Do not add a discount, use an independently measured average object size, or subtract both worst-case small-object loss and the full monitoring fee: the native formula takes the lower of two bounds. Existing monitoring credit is capped by IT bytes but is not otherwise clamped. This is a conservative approximation, not an object-size distribution. Candidate count can still contain GIR, One Zone-IA, sub-128-KiB IT objects, delete markers and multipart parts; the native code does not identify them individually.

For `HasObjectMetrics`, compute `totalSizeGb` as the sum of all seven tier totals. `metricGaps` contains, in this order when applicable: `number-of-objects-missing` if latest count undefined; `number-of-objects-zero` if defined and <=0; `bucket-size-zero` if totalSizeGb is not >0. When this list is nonempty, `reason="cloudwatch-metric-gap"`. Otherwise choose the first matching reason below:

1. `glacierGb+deepArchiveGb > 0 && A===0` → `archived-object-overhead-missing`.
2. If `T<=0`, return `transitionable-bytes-missing` only when `resource.dataSavings>0 && (resource.stdGbMonths>0 || (resource.iaGbMonths>0 && onezoneIaGb<=0))`; otherwise return no reason immediately. Do not continue to conditions 3–4 in this branch.
3. `N-A < 1` → `no-objects-outside-archive`.
4. `candidateObjectCount < 1` → `monitored-objects-reach-object-count`.
5. Otherwise no reason; coverage passes.

Save a fee estimate for this resource row only when coverage passes. Its complete fields are `objectCount=N`, `transitionableSizeGb=T`, `monitoredObjectCount`, `candidateObjectCount`, `averageObjectSizeKb`, `existingMonitoringFee=E`, `monitoringFee`, `smallObjectShare`, `netSavings`, `transitionCharge`, `firstYearNetSavings`. Two economic rows for one bucket need distinct estimates even though their metrics match.

`HasObjectMetrics.value` is `{reason,metricGaps,objectCount:<latest value or omitted>,archivedObjectCount:A,totalSizeGb,transitionableSizeGb:T,monitoredObjectCount,candidateObjectCount,curDataSavingsUsd:resource.dataSavings,curStdGbMonths:resource.stdGbMonths,curIaGbMonths:resource.iaGbMonths}`.

`NetSavingsAfterObjectFees.value` is `{transitionableSizeGb:T,candidateObjectCount,averageObjectSizeKb,minObjectSizeKb:128,smallObjectFraction:smallObjectShare,annualMonitoringFeeUsd:monitoringFee,annualNetSavingsUsd:netSavings,transitionChargeUsd:transitionCharge,firstYearNetSavingsUsd:firstYearNetSavings}`. It fails fast when first-year savings are <=0, even if steady-state savings are positive.

For native supplied/CUR quantitative rows, native post-validation population sets `annualSavings = estimate.netSavings` when a saved estimate exists, otherwise `resource.totalSavings`; `serviceUsage=resource.gbMonths`; and `resourceFixedConfiguration={intelligentTiering:"enable"}`. **The gross-savings fallback on a stopped/rejected row is incidental output, not an approved net saving.** Preserve it as `nativeCalculation.annualSavings` with its origin, and do not present it as a usable recommendation. MaximumSavings compares the steady-state net figure with `annualCost`; the initial transition charge affects order 10, not that comparison.

Keep full calculation precision. `projectedAnnualCost=annualCost-annualSavings`, and `otherStorageGb=gbMonths-stdGbMonths-iaGbMonths-girGbMonths`, without clamping.

# Costing for unattached EBS volumes

Read this in stage 2 of `SKILL.md`, before the price reads of any priced mode, and in stage 4 in every pricing mode. The archive price lookup and the savings formula apply to `supplied` and `billing` mode. `billing` covers both sources of billed cost, `cost-explorer` (`billing-cost-explorer.md`) and `cur` (`cur-recipe.md`): each supplies the `annualCost` and uses this lookup and formula unchanged. `immediateArchival` and the snapshot context apply to every mode, and the savings checks to every priced mode. In `public_prices` mode read `public-price-model.md` as well.

## Native archive snapshot price

For `supplied-native-resource`, `cost-explorer` or `cur`, resolve the native archive snapshot price as follows. For `public-price-model`, use the separately labelled regional lookup in `public-price-model.md`; an optional native comparison must retain both lookups and outputs. Source `GetProducts` uses `ServiceCode:"AmazonEC2"`, `MaxResults:1`, and TERM_MATCH filters: `productFamily:Storage Snapshot`, `regionCode:<region>`, `storageMedia:Amazon S3`, `usagetype:EBS:SnapshotArchiveStorage`, plus the region's official Pricing `location`. Preserve the exact returned SKU, terms, unit, USD rate and request. This native lookup reads one response without following its NextToken. It selects the first returned PriceList JSON containing `GB-Mo`, then the first OnDemand price dimension in `GB-Mo` with a USD price, skipping descriptions containing `(free tier)`. If lookup returns undefined or throws, native fallback is **0.0125 USD/GB-month**. Preserve the failure/miss and label fallback; never claim it was a retrieved regional quote. The exact unprefixed usage-type filter misses region-prefixed archive SKUs. In such regions the 0.0125 fallback can understate archive cost and overstate savings. Native parity does not make this rate regionally correct. A supplied resolved rate needs the same provenance or an explicit simulated-input label. A missing unattempted lookup is an evidence gap, not a fabricated exception.

## Savings formula

Execute, without intermediate rounding, for **every item**, even a native-rejected one:

```text
volumeSize = normalizedVolume?.Size ?? 0
archiveSnapshotAnnualCost = archiveSnapshotPrice * volumeSize * 12
annualSavings = max(0, annualCost - archiveSnapshotAnnualCost)
newCost = annualCost - annualSavings
immediateArchival = VolumeType in [st1, sc1, standard]
```

If annualCost is missing, leave these cost outputs unknown rather than allowing the native initialized zero to appear as an estimate. Retain explicit supplied Size zero or native missing-Size fallback zero in the projection, with the source omission qualified. Missing size must not become an unqualified full-cost savings claim. The source uses **archive pricing for every supported type**, even when `immediateArchival=false`; it does not cost existing snapshots, initial standard-tier residence, minimum archive duration, retrievals or deletion fees. `newCost` can differ from computed archive cost because savings are clamped. Do not silently “correct” the formula.

## Savings checks 7 and 8

Run source savings checks 7–8 on items not stopped. Native `commonPostValidation` clears stopProcessing for approved items before these guards. A zero saving fails default minimum 0. On native rejection final cleanup removes fixerParameters, but leaves incidental annualSavings/report data for this finder; exclude those numbers from recommended-savings totals.

## Snapshot context

Native normalization drops records missing/null `VolumeId`, `SnapshotId`, `StartTime`, `StorageTier` or `State`; Tags defaults to `[]`. Group by VolumeId and sort descending StartTime. For approved items, report every `{snapshotId,startTime,state,volumeSize:VolumeSize??0,description:Description??""}`, and `hasExistingSnapshots` from array length.

Native snapshot API failure is swallowed, producing empty snapshot report data. Preserve that projection **and** the error/unknown in the assessment; do not tell the user “no snapshots exist” after a failed or incomplete read. Snapshot presence never changes eligibility, savings or immediateArchival. The report's suggestion that an existing snapshot may suffice is not a verified recovery plan or permission to skip backup.

## Saving the result in `assessment.json`

- In `supplied` and `billing` mode `costs.current_annual` is `annualCost`, `costs.target_annual` is `newCost` and `costs.annual_savings` is `annualSavings`, each unrounded, in USD. `costs.basis` and the other fixed values per pricing mode are in `assessment-guide.md`.
- `costs.status` is `calculated` only when the formula ran on a known annual cost in USD, a normalized `Size` and a resolved archive rate, and rules 7 and 8 were evaluated on its results. Otherwise it is `not_calculated` with `annual_savings` left out; keep a known current cost with its `basis` and `currency`. The finder's incidental figures for a stopped item are not an estimate and are not saved as `costs`.
- `details.archive_rate` is the archive rate used, in USD per GB-month, `details.archive_rate_source` is `native_lookup`, `fallback`, `regional_quote` or `supplied`, and `details.archive_annual` is the computed archive cost. When the rate is the **0.0125** fallback, say so in the row's `costs.qualifications`. When any row uses it, also add one top-level qualification naming those volumes and saying their savings depend on the fallback rate.
- `details.snapshots`, on a recommended row, is the normalized list for the volume: `[]` after a completed read that returned none, and `null` when the snapshot read failed or is incomplete. A failed snapshot read also gets an `issues` entry of kind `read_error` naming the read; it never changes a decision.
- `recommendation.parameters.immediate_archival` is `immediateArchival`.
- Save the formula's inputs, expressions and unrounded results in `evidence/calculations.json` under the volume ID, as `native_formula`. With Cost Explorer costs, `billing-cost-explorer.md` names the further fields and qualifications.

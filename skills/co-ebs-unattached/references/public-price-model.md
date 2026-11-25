# Public-price model and supported regional archive estimate

Read this in stage 2 of `guided.md`, before its catalog reads, and again in stage 4, only when the requested pricing mode is `public_prices` (`economicsMode:"public-price-model"`), or when the user explicitly asked for a public-price comparison beside a `supplied` or `billing` result. It also serves the conditional request of `billing-cost-explorer.md`, after Cost Explorer could not produce a rate and the customer's own request allowed public prices.

Use this path only for the chosen `public_prices` mode or an explicitly requested comparison. Keep the native-formula result and its supplied/CUR inputs untouched. Save the following as `public_price_model` (current public capacity/performance costs) and `regional_estimate` (the archive model using a verified regional rate). The second can also use an unchanged trusted USD annualCost if that comparison was requested; label its basis. Neither is native finder output.

1. **Verify placement.** Use the volume's actual region and availability-zone evidence. The queries below support ordinary regional AZs, not automatically Outposts or Local Zones. Do not infer a regional rate from a truncated AZ name. Unsupported/unknown placement leaves economics unresolved, without changing technical eligibility.
2. **Collect all price components.** Call `GetProducts` with `ServiceCode:"AmazonEC2"`, `FormatVersion:"aws_v1"`, `MaxResults:100` and `TERM_MATCH` filters `regionCode:<volume region>`, `locationType:"AWS Region"`, `volumeApiName:<type>`. The Pricing endpoint is normally us-east-1; it is not the priced region. Fetch every `NextToken` with the same filters, retaining all requests, pages, retries and errors. Repeated/missing/expired tokens leave the attempt incomplete; an expired token can start a separately recorded traversal. Parse every PriceList JSON string and select the components below.

| Component | Required productFamily / group | Required unit |
| --- | --- | --- |
| Capacity for all seven supported types | `Storage` / `EBS Storage` | `GB-Mo` |
| gp3 or io1 IOPS | `System Operation` / `EBS IOPS` | `IOPS-Mo` |
| gp3 throughput | `Provisioned Throughput` / `EBS Throughput` | `GiBps-mo` |
| io2 first/second/third IOPS band | `System Operation` / `EBS IOPS`, `EBS IOPS Tier 2`, `EBS IOPS Tier 3` | `IOPS-Mo` |
| Magnetic (`standard`) operation usage | `System Operation` / `EBS I/O Requests` | `IOs` |

3. **Resolve each quote.** Require matching region/type and one compatible OnDemand USD quote per applicable component. Save SKU, all attributes, term code/effective date, dimension rate code/description/unit/beginRange/endRange/appliesTo and raw decimal rate. Identical repeated records may be deduplicated for calculation, retaining all raw pages; conflicting duplicates or multiple compatible quotes are ambiguous, not permission to pick the first. Require finite nonnegative rates, correct units, applicable dates and flat quantity coverage. Unexpected tiering/conditions require evidence, not guessed interpretation. For io2, the three separate group-identified products can each say 0–Inf: use the documented per-volume 32,000/64,000 marginal bands below, never one lowest rate for all IOPS. Replay uses recorded quotes, not current quotes relabelled historical.
4. **Resolve regional archive storage separately.** Query all pages with the same region/locationType filters and `productFamily:"Storage Snapshot"`, `storageMedia:"Amazon S3"`. Keep only usage types exactly `EBS:SnapshotArchiveStorage` or ending `-EBS:SnapshotArchiveStorage`, and validate `snapshotarchivefeetype=SnapshotArchiveStorage` when present. Require one compatible OnDemand USD `GB-Mo` dimension. Retrieval/early-deletion/standard-tier SKUs do not match. Missing/ambiguous archive pricing leaves `regional_estimate` unavailable; there is **no fallback** in this path. Retain the native unprefixed lookup and fallback separately if a native comparison was requested.
5. **Calculate current provisioned cost.** Use `Size` in GiB as the provisioned-capacity quantity against the catalog GB-Mo rate, preserving both unit labels. `Iops` is required for gp3/io1/io2 and `Throughput` (MiB/s) for gp3. Do not assume missing settings are the included baseline. gp2 Iops is not separately billed; io1/io2 throughput is not a separate component. Magnetic Iops is not provisioned-IOPS billing. Execute the following calculator function after validated price resolution; it is the public extension, not replacement native code.

```javascript
// public-ebs-cost-calculation
function estimateEbsPublicCost(v, r, annualIoCount = null) {
  const nonnegative = (x, name) => {
    if (typeof x !== 'number' || !Number.isFinite(x) || x < 0) throw new Error(`Missing or invalid ${name}`);
    return x;
  };
  const S = nonnegative(v.Size, 'Size');
  if (!(S > 0)) throw new Error('Public estimate requires positive Size');
  const charge = (quantity, rate, name) => quantity === 0 ? 0 : quantity * nonnegative(rate, name);
  let monthlyCost = charge(S, r.storage, 'storage rate');
  switch (v.VolumeType) {
    case 'gp2': case 'st1': case 'sc1': break;
    case 'gp3': {
      const I = nonnegative(v.Iops, 'Iops'), T = nonnegative(v.Throughput, 'Throughput');
      monthlyCost += charge(Math.max(I - 3000, 0), r.iops, 'IOPS rate')
        + charge(Math.max(T - 125, 0) / 1024, r.throughput, 'GiBps-mo rate');
      break;
    }
    case 'io1':
      monthlyCost += charge(nonnegative(v.Iops, 'Iops'), r.iops, 'IOPS rate');
      break;
    case 'io2': {
      const I = nonnegative(v.Iops, 'Iops');
      monthlyCost += charge(Math.min(I, 32000), r.iops1, 'IOPS band 1')
        + charge(Math.min(Math.max(I - 32000, 0), 32000), r.iops2, 'IOPS band 2')
        + charge(Math.max(I - 64000, 0), r.iops3, 'IOPS band 3');
      break;
    }
    case 'standard': {
      const annualStorageSubtotal = 12 * monthlyCost;
      if (annualIoCount === null) return {annualStorageSubtotal, annualPublicCost: null, missing: ['annual I/O quantity']};
      const annualOperationEstimate = charge(nonnegative(annualIoCount, 'annual I/O quantity'), r.io, 'price per IOs');
      return {annualStorageSubtotal, annualOperationEstimate, annualPublicCost: annualStorageSubtotal + annualOperationEstimate, missing: []};
    }
    default: throw new Error('Unsupported volume type');
  }
  return {monthlyPublicCost: monthlyCost, annualPublicCost: 12 * monthlyCost, missing: []};
}
```

Magnetic operation price is per **single IOs unit** in the raw catalog. Do not divide it by a million because the description mentions a million. DescribeVolumes does not supply annual I/O count. Without trusted compatible operation-count evidence or an **explicit** remain-unattached/no-future-I/O forecast, keep its storage subtotal and unknown operation component; do not call that a complete annual cost. A forecast of zero future I/O is an assumption, never measured historical usage. Do not reuse the timestamp-only attachment metric interpretation as a billing-quantity collector. A zero-quantity component costs zero without inventing a quote; explain why its quantity is zero.

6. **Calculate the regional archive-model comparison.** With verified `size`, `regionalArchiveRate` and `supportedAnnualCost` in USD, compute without intermediate rounding:

```javascript
// regional-ebs-archive-calculation
function estimateRegionalArchive(size, regionalArchiveRate, supportedAnnualCost) {
  if (![size, regionalArchiveRate, supportedAnnualCost].every(x => typeof x === 'number' && Number.isFinite(x) && x >= 0) || size === 0)
    throw new Error('Regional estimate requires supported size, rate and cost');
  const archiveAnnualEstimate = 12 * size * regionalArchiveRate;
  const annualSavingsEstimate = Math.max(0, supportedAnnualCost - archiveAnnualEstimate);
  return {archiveAnnualEstimate, annualSavingsEstimate, newCostEstimate: supportedAnnualCost - annualSavingsEstimate};
}
```

Apply the same strict minimum and maximum savings comparisons only in labelled `model_checks` for this extension. Do not populate native financial validations or claim native-equivalent financial approval with substituted costs/rates. Keep native technical checks, their order, known rejections and unknown history. The requested public estimate can be complete for supported inputs even when native financial/context results are unavailable; say which conclusion is supported.

The archive estimate remains a volume-capacity proxy. Archived snapshots are billed for stored full-copy blocks; volume Size does not establish snapshot billable size. The native formula uses archive cost even for types not immediately archived. Disclose unpriced standard-tier residence, 90-day archive minimum duration, restore/early-deletion charges, existing snapshots, request/copy features, discounts, taxes and future configuration/price changes. Do not invent a first-year invoice or a recovery plan. Report current-size 12-month **public run rate**, not the CUR recipe's 365/31 historical annualization.

Official pricing and units: [EBS pricing](https://aws.amazon.com/ebs/pricing/), [Volume fields](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_Volume.html), [GetProducts](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_pricing_GetProducts.html).

## Saving the result in `assessment.json`

- In `public_prices` mode `costs.basis` is `public_price`, `costs.current_annual` is `annualPublicCost` (the `supportedAnnualCost` given to the archive calculation), `costs.target_annual` is `newCostEstimate` and `costs.annual_savings` is `annualSavingsEstimate`, each unrounded. There is no `source_period`.
- Rules 7 and 8 in `checks` hold the model checks, not native financial validations. Begin each `reason` with `model_checks:` and name the public-price estimate that was compared, also when the check passes.
- `details.archive_rate` is the regional archive rate, `details.archive_rate_source` is `regional_quote` and `details.archive_annual` is `archiveAnnualEstimate`.
- Save both calculator calls in `evidence/calculations.json` under the volume ID, as `public_price_model` and `regional_estimate`, each with its `input` (and `rates`) and its `result`.
- A magnetic volume without an annual I/O quantity, an unsupported placement and a missing or ambiguous quote leave `costs.status:"not_calculated"`. Keep the storage subtotal in `details.public_price_model`, never as `current_annual`. The guide gives the decision and the `issues` entry.
- For a comparison requested beside a `supplied` or `billing` result, leave `costs` on the requested basis and keep both objects in `details.public_price_model` and `details.regional_estimate`.

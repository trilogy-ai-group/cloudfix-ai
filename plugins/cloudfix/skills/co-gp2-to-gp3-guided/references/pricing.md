# Price lookup and annual price arithmetic

Read this at stage 1 of `SKILL.md`, before the four Pricing lookups, and use it again at stage 4. It holds the finder's exact price requests, selection rules, fallbacks and the annual price formula.

## Price lookup and fallbacks

Use explicit supplied price records when available, preserving their values and provenance. That substitutes the **pricing retrieval**, not its arithmetic. Otherwise retrieve the applicable regional AWS EBS prices and retain the query, currency, units and date. The source uses monthly storage per GiB, IOPS per IOPS-month and throughput per MiB/s-month.

To reproduce the source lookup, use AWS Pricing `GetProducts`, `ServiceCode=AmazonEC2`, `MaxResults=1`, and `TERM_MATCH` filters. The source adds the AWS Pricing `location` name mapped from the requested region; supply a verified matching location rather than guessing the name. Storage filters are `volumeType=General Purpose`, `volumeApiName=gp2` or `gp3`, and `regionCode=<region>`, with unit `GB-Mo`. gp3 IOPS filters are `volumeApiName=gp3` and `group=EBS IOPS`, with unit `IOPS-Mo`; throughput uses `volumeApiName=gp3` and `group=EBS Throughput`, with unit `GiBps-mo`. The mapped location filter applies to all three.

The original lookup does not paginate these price queries. It takes the first `PriceList` string containing the requested unit, decodes it, and selects the first matching exact-unit USD dimension under `OnDemand`, excluding dimensions whose description contains `(free tier)`. Record the selected SKU/dimension and fallback provenance. This is the source's selection behavior, not a guarantee that an underspecified query always selects the intended price. Do not silently substitute a different selection algorithm and claim exact source parity.

The exact source retrieval behavior is:

| Field | gp2 | gp3 |
| --- | --- | --- |
| Storage rate | Retrieved rate, or `.10` if null/undefined | Retrieved rate, or `.08` if null/undefined |
| IOPS rate | `0` | Retrieved `IOPS-Mo`, or `.005` if null/undefined |
| Throughput rate | `0` | Retrieved `GiBps-mo` divided by `1024` when truthy; otherwise `.06` |
| Included IOPS / throughput | `0 / 0` billing fields | `3000 / 125` |

These fallback amounts are USD and reflect the code's US-East defaults, not independently verified prices for the customer's region. Explicit zero storage/IOPS is retained by `??`; zero throughput from the GiBps lookup triggers `.06` through the source's truthiness check. The defaults table also contains `.04` for throughput, but gp3's missing-price path uses the helper's **`.06`**, not that table entry. Label any fallback explicitly. A supplied resolved throughput price, including zero, is already an input to the arithmetic; do not reapply retrieval fallbacks to it. A failed/denied lookup is not evidence that a lookup returned no price.

## Annual price arithmetic

The source's zero gp2 I/O charge fields describe billing, not zero gp2 performance. Do not add gp2 performance charges. Use this annual-price arithmetic from `getDiscountedVolumePrice`, for each type:

```ts
const clampedDiscount = Math.min(Math.max(discountRatio, 0), 1);
const storageCost = size * pricing.storagePrice * clampedDiscount * 12;
const iopsValue = pricing.iopsPrice > 0 ? Math.max(0, iops - pricing.freeIops) : 0;
const iopsCost = iopsValue * pricing.iopsPrice * clampedDiscount * 12;
const throughputValue = pricing.throughputPrice > 0 ? Math.max(0, throughput - pricing.freeThroughput) : 0;
const throughputCost = throughputValue * pricing.throughputPrice * clampedDiscount * 12;
```

`finalPrice = storageCost + iopsCost + throughputCost`. The finder calls this for gp2 with `(size, discountRatio=1, gp2Iops, throughput=0)` and gp3 with `(size, discountRatio=1, gp3Iops, gp3Throughput)`. These give `gp2Public.finalPrice` and `gp3Public.finalPrice`.

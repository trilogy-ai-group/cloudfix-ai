# Check rules: control flow, rule 3 counting, rule 4 and failure boundaries

Read this in stage 3 of `guided.md`, before evaluating the first distribution. The ordered check table is in `SKILL.md`; this file is the rest of the finder's rule text.

The text below describes the finder's native state: `approved`, `stopProcessing`, validation rows, `fixerParameters`. That state decides which checks run and how each ends. It is written out only in the optional native-shaped projection (`references/native-projection.md`). In `assessment.json` a check is `pass`, `fail` or `not_evaluated` with a reason, and `references/assessment-guide.md` maps every outcome below to a decision.

## Control flow

Initialize each projection with the original `resource`, `approved` undecided, `stopProcessing:false`, `annualSavings:0`, `finderReportData:{}`, `otherImpactedNodeIds:[]`. A fail-fast failure sets `approved:false,stopProcessing:true`; later finder stages skip that item. Ordinary failure sets `approved:false` and continues. A later pass never reverses failure. Keep skipped/unavailable checks separately from native validation rows.

## Rule 3: counting policies and behaviors

For rule 3, a fetched unique policy disables caching iff its exact Name is **`Managed-CachingDisabled`**, or **DefaultTTL, MaxTTL and MinTTL are each strictly numeric `0`**. `policyCount` is the number of fetched unique policies; `cachingDisabledPolicyCount` counts those matching either condition. **No policy IDs means policyCount=0 and the policy portion passes.** Legacy behavior TTLs/ForwardedValues are ignored by this finder. A custom policy with Name/MinTTL present and other TTLs absent passes the policy portion unless the name matches. Preserve these source limitations.

Collect default `Compress` followed by additional behavior Compress values; omit only null/undefined. `behaviorCount` counts those retained values, not all behaviors. `compressionDisabledBehaviorCount` counts JS-falsy retained values (for valid AWS booleans, explicit false). Thus all missing Compress values yield `0/0` and fail; an explicit false plus a missing Compress can pass. Retain invalid types as data-quality issues alongside the source projection, not new invented native validations.

No traffic metric, distribution-enabled-state test, Brotli/Gzip policy-flag test or content-type test is added to the checks or used to reject a finding.

## Finalization and rule 4

After rule 3, native finalization approves when no recorded row failed. It temporarily sets `{distributionId:resourceId}` as fixerParameters on every item, then copies distribution tags for items not stopped. Common post-validation clears stopProcessing on approved items and applies rule 4. `0` cost passes this maximum guard, negative cost fails, NaN cost fails, and infinite cost has native comparison semantics but is not a usable estimate. `reason:"annual-cost-not-finite"` accompanies a supplied nonfinite cost; an absent undefined cost is omitted from JSON and has no reason from that helper. JSON cannot hold NaN or infinity: in `assessment.json` write such a cost as `annualCost: null` with that reason. Record any nonfinite/missing fact outside native rows as a financial evidence issue. Source initialization of final savings to zero is not a fallback cost estimate.

## Failure boundaries

**Failure boundaries matter.**

- A policy read/normalization exception inside rule 3 creates a fail-fast `Exception` / `Other` row with `{context:"Validation error: ",error:<message>,errorName:<name>,stack:<actual stack or null>}`. Native error serialization takes at most the first 4,096 UTF-8 bytes of an available stack; retain the complete original stack separately, and never invent one. A thrown non-Error has `errorName:null`. Rule 3's counts/report fields are not populated, rule 4 is skipped, and other distributions continue. This includes policy AccessDenied.
- A distribution-config nonpermission exception occurs before those per-item stages and fails the native batch; no successful complete result exists.
- A distribution-config permission denial takes the base framework's batch denial path: fresh initialized items with a single false row named after the error, type Other, value `{error,errorName}`; approved remains undecided and stopProcessing false. The native platform would retire carried recommendation IDs, but this skill performs no such writes. Identify the boundary and missing evidence instead.
- Missing supplied reads are not recorded exceptions and must not be manufactured into these rows.

**Exact per-item exception contract.** A fault caught inside a wrapped validation callback uses `context:"Validation error: "`. A fault caught inside parameter population uses `context:"Error while populating parameters: "`. Both literal strings end with one space; retain it. The row is `{name:"Exception",type:"Other",approved:false,value:<serialized {context,error,errorName,stack}>}` and sets the item `approved:false,stopProcessing:true`. For an actual Error, `error` is its message and `errorName` its name; for a thrown non-Error, `error=String(thrownValue)`, `errorName:null`, `stack:null`. Preserve the observed stack when available under the native serialization limits, otherwise null; never invent a stack or infer an unrecorded error class. Preserve full diagnostics separately. Only an evidenced thrown fault at the stated boundary produces this row: a missing unattempted read or an ordinary failed validation is not an Exception. Later source stages retain their existing skip, savings and cleanup rules.

The policy-read/normalization failure above is a validation callback. Parameter assignment uses the parameter-population context. Distribution-configuration batch failures and swallowed tagging failures retain their separate behavior; do not convert them into per-item Exceptions.

## Supporting download-traffic observation

`BytesDownloaded` is optional supporting context, not an eligibility check. Rules 1 to 4 decide the finding. Never add a fifth check, and do not replace a configuration/policy `error` or `needs_evidence` with `not_recommended` because a download metric is zero or unavailable.

**Coverage.** The native finder discovers from the CUR, not `ListDistributions`. It keeps distributions with `Data Transfer` line items in the billing week `date_trunc('week', CURRENT_DATE - interval '8' day)` to `date_trunc('week', CURRENT_DATE - interval '1' day)` and a weekly net cost annualized (`/7*365`) above the tenant's `resourceCostThreshold`. `BytesDownloaded` measures only GET and HEAD responses. A POST-only distribution can have billed Data Transfer while showing zero downloads. A measured sum of zero does not establish that billed Data Transfer was zero; an empty series does not even establish a measured sum. Without the CUR and threshold, inventory discovery is not native discovery parity. Keep that qualification even when the metric is positive.

**Input.** The optional stage 2 `GetMetricData` read in `us-east-1`: namespace `AWS/CloudFront`, metric `BytesDownloaded`, dimensions `DistributionId=<id>` and `Region=Global`, `Stat: Sum`, `Period: 86400`, `StartTime`/`EndTime` the billing week, Monday 00:00 UTC to the next Monday 00:00 UTC, end exclusive. Follow every `NextToken`, preserve all pages and sum the returned datapoints only after completing the series. An earlier `PartialData` may be followed by a complete page; it does not itself promise another page. A final `PartialData` without a next page is incomplete, not a measured zero or a complete week.

**Observation.** In `details.download_traffic` retain `{bytesDownloaded,weekStart,weekEnd,datapoints,reason?}`. For a fully collected series with final status `Complete` and at least one valid datapoint, `bytesDownloaded` is the sum of returned values, including an explicit sum of zero. It describes the returned GET/HEAD datapoints, not all traffic or profitability. Keep the actual returned datapoint count and reference the full read in the finding's `evidence_refs`. An explicit complete result with no datapoints means unknown, not measured zero: set `bytesDownloaded:null`, `datapoints:0` and a reason.

**Unavailable or incomplete.** A denied/failed call, `InternalError` or `Forbidden` on any page, a query ID missing from every page, final status other than `Complete` (including absent status or terminal `PartialData`), or unfinished pagination leaves `bytesDownloaded:null`. Keep any returned datapoints and statuses in the original evidence; the observation's `datapoints` is their actual count, or null when no result was available. Never substitute a partial sum or empty array for the unknown total, and never treat a failed read as zero bytes. Add a `qualification` issue naming affected distributions, the exact read and the missing/failed/incomplete condition. The same applies when a requested or supplied metric read is missing. If traffic context was not requested and no metric was supplied, omit `details.download_traffic` and do not create a missing-evidence issue for this optional read.

Zero, absent, denied or incomplete metrics leave the finding's decision and coverage unchanged. They never establish that compression has no value, never prove safe workload behavior, and never appear in the native-shaped projection.

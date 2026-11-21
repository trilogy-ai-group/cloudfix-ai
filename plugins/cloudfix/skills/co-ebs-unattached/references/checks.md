# Checks for unattached EBS volumes

Read this at the start of stage 3 of `SKILL.md`. It is the finder's own rule text: normalization, the eight ordered checks, the CloudWatch interpretation, finalization and the exception contract. Apply it as written.

Sentences about projections, native report data, fixer parameters and native-shaped rows say what the finder itself records. They fix the control flow (`approved`, `stopProcessing`) and the facts to keep. Save those facts in `assessment.json` as the last section describes; produce the native shape itself only when the user explicitly asks for it (`native-projection.md`).

## Normalization and control flow

Keep raw responses and normalized projections separately. Native volume-map normalization requires non-nullish `VolumeId`, `VolumeType` and `State`, drops entries missing any, and uses the last entry for a duplicate ID. Size and CreateTime are not map-required. A raw missing State/type therefore produces an absent normalized volume; do not mistake service-mocked test behavior for this raw-input path. A batch `DescribeVolumes` permission denial is not absence: the native framework resets the batch projections to fresh initialized rows and adds one failed validation `{name:error.name,type:"Other",approved:false,value:JSON text of {error:error.message,errorName:error.name}}` per resource, leaving `approved` undecided and `stopProcessing:false`. Preserve complete original diagnostics and mark the harness run `read_error`; do not produce `ResourceNotDeleted` or claim deletion. Other uncaught batch errors fail the assessment. The standalone run never writes recommendation status.

Initialize each projection with `approved` undecided, `stopProcessing:false`, `annualSavings:0`, `finderReportData:{}`, `otherImpactedNodeIds:[]`. A fail-fast failure sets `approved:false,stopProcessing:true`; subsequent validation stages omit that item. An ordinary failure sets `approved:false` and continues. Passing a row never reverses an earlier failure. Record skipped/not-evaluated checks separately from native rows.

## The eight checks, in order

| Order | Native validation / type | Exact value and pass condition |
|---|---|---|
| 1 | `RecommendationFinalized` / Other | `{resourceId}`; pass iff no same-resource, same-finder history row has `savingsFinalizedAt > asOf - 14 days`. The lower boundary is strict and preserves time of day; exact equality does not match, a future timestamp does match. There is no status filter or upper bound. Ordinary failure. This is CloudFix context: if unavailable, record `not_evaluated`, never invent a lookup/pass. It does not prevent a standalone resource assessment; full-framework equivalence remains unknown. |
| 2 | `ResourceNotDeleted` / Resource Fixed | `{resourceId}`; normalized volume map contains this ID. Fail-fast. Native absence fulfills the delete recommendation and is classified manually fixed, not proof of who deleted it. |
| 3 | `IsValidState` / Other | `{volumeState,requiredState:"available"}`; state equals `available`. Fail-fast. No separate Attachments-array check. |
| 4 | `VolumeExpectedType` / Other | `{volumeType,expectedTypes:["gp2","gp3","io1","io2","st1","sc1","standard"]}`; exact membership. Fail-fast. |
| 5 | `VolumeAgeXDays` / Other | `{createTime,ageDays,minAgeDays:30,reason}`; whole elapsed UTC days, truncating toward zero, from CreateTime to asOf must be `>=30`. Missing CreateTime gives undefined age and `reason:"create-time-unreported"`, and fails. Ordinary failure. |
| 6 | `DetachedXDaysAgo` / Other | Metrics procedure below; fail-fast. |
| 7 | `MinimalSavings` / Savings Threshold | `{annualSavings,annualCost,minimalSavings,reason}`; savings **strictly greater** than configured minimum. Ordinary failure, after savings calculation. |
| 8 | `MaximumSavings` / Savings Threshold | `{annualSavings,annualCost,reason}`; savings **<= annualCost**. Ordinary failure. |

For invalid dates/nonnumeric values retain the original evidence and data-quality issue; do not coerce them to an invented valid date/cost. Native invalid CreateTime yields NaN age and fails its comparison. Nonfinite savings reason is `annual-savings-not-finite`; the maximum check also reports `annual-cost-not-finite` when applicable. These reason fields do not add a new finite-number guard to native comparison semantics; standalone output must not present nonfinite cost/savings as a valid estimate.

After step 5, native report data gets `volumeType` and `volumeState`. Non-fail-fast age/finalization failures still continue to metrics. The metrics call includes every not-stopped resource with available normalized state.

Rules 7 and 8 depend on savings and are completed in stage 4 (`costing.md`).

## Exact CloudWatch interpretation

Native service checks every raw page for `StatusCode` `InternalError` or `Forbidden`; either throws. It rejoins a series across pages by `(Id,Label)` in received order, concatenating timestamps/values and retaining the final status. Results without IDs and duplicate same-ID/same-label series on one page stay separate. Final `PartialData` is returned without rejection by this API. Any thrown call causes every volume in this helper invocation to receive `detectionMethod:"error"`. Do not import stricter checks from CloudFix's other metrics API.

For each volume, skip a result missing Id, Values or Timestamps; an empty Values array is present. Derive its volume ID by dropping the first underscore-separated component and replacing remaining underscores with hyphens. From every matching series with a nonempty Timestamps array consider **only its first timestamp**; select the latest of those dates. The native code trusts descending order and never inspects values: **zero is attachment evidence**. It does not demand every expected series, equal array lengths or positive activity. Retain malformed/partial-response qualifications without silently changing these rules.

- Date found: `detectionMethod:"cloudwatch"`, `lastAttachmentDate` is that date.
- Completed successful query but no usable matching timestamp: `detectionMethod:"no_data"`.
- Recorded query exception/status fault: `detectionMethod:"error"`.
- Query not performed, missing pages or missing supplied response: harness `needs_evidence`; never fabricate `no_data`.

Set `maxLastAttachmentDate=asOf minus volumeAgeThreshold days`, preserving time of day. `error` fails; `no_data` passes; otherwise a present attachment date passes only when **strictly before** the cutoff (equality fails), and an absent date passes. The row value is `{detectionMethod,lastAttachmentDate,maxLastAttachmentDate}`. Native report adds `detachedSince=lastAttachmentDate` and `metricsLookbackStartDate=asOf minus 365 days` (unrounded, distinct from query StartTime). Native no-data interpretation is “detached before the lookback”; report this as the finder inference, not independent proof of inactivity.

## Finalization

After these checks, native finalization approves only if no recorded row failed. For a standalone run, retain any unevaluated CloudFix-context row separately: do not turn it into a native pass. Only approved resources receive fixer parameters, copied volume tags and snapshot report details. Ordinary age/finalization rejection can still receive savings rows; fail-fast rejection skips them.

## Exceptions

**Exact per-item exception contract.** A fault caught inside a wrapped validation callback uses `context:"Validation error: "`. A fault caught inside parameter population uses `context:"Error while populating parameters: "`. Both literal strings end with one space; retain it. The row is `{name:"Exception",type:"Other",approved:false,value:<serialized {context,error,errorName,stack}>}` and sets the item `approved:false,stopProcessing:true`. For an actual Error, `error` is its message and `errorName` its name; for a thrown non-Error, `error=String(thrownValue)`, `errorName:null`, `stack:null`. Preserve the observed stack when available under the native serialization limits, otherwise null; never invent a stack or infer an unrecorded error class. Preserve full diagnostics separately. Only an evidenced thrown fault at the stated boundary produces this row: a missing unattempted read or an ordinary failed validation is not an Exception. Later source stages retain their existing skip, savings and cleanup rules.

Volume-map validation and report callbacks use the validation context; parameter/tag population uses the parameter context. DescribeVolumes batch denial retains its fresh batch error-row behavior above. The caught CloudWatch metrics failure, archive-price fallback and swallowed snapshot failure keep their stated outcomes; they do not become these Exception rows.

## Recording the checks in `assessment.json`

Each finding's `checks` holds all eight rules, in the order above, under the names in the table. `value` is the rule's value object as JSON (not JSON text). The status is:

- `pass` when the rule was evaluated and passed.
- `fail` when the rule was evaluated on observed evidence and failed. Give the `reason` in plain words with the observed values.
- `not_evaluated`, always with a `reason`, in every other case: a rule omitted after a fail-fast stop; rule 1 when CloudFix recommendation history is not available; rules 7 and 8 without a usable cost, or in `technical_only` mode; rule 6 when the metrics query was not performed or is incomplete.

Two native outcomes are read faults, not properties of the volume, and are therefore `not_evaluated` rather than `fail`: rule 6 with `detectionMethod:"error"`, and the rule whose callback threw a per-item exception. Keep `detectionMethod:"error"` in the rule's `value`, say in the `reason` which read failed and that the finder itself records a failed row, and add the `issues` entry the guide describes. A `DescribeVolumes` batch denial leaves all eight rules `not_evaluated` for every volume of that batch. Never infer that an unobserved check passed.

Record for missing framework history `details.framework_context_complete:false`. A demonstrated technical rejection stays visible even when cost is unknown.

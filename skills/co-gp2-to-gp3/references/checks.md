# Checks, control flow and proposed settings

Read this at stage 3 of `guided.md`, before evaluating any volume. It holds the finder's ordered checks, their exact value shapes, the elastic-volume decision tree, fail-fast and exception behavior, and the sizing calculation.

## Check ledger

Keep a check ledger with the native name, inputs, result and reason. Preserve the order and conditional behavior. The code-based local evaluator has nine rows, including one adapter-provided history assumption. Reproduce that algorithm row while distinguishing it from actual history verification. In `assessment.json` it is a run-wide qualification, not a check (see `assessment-guide.md`).

| Order | Native check | Source-defined rule |
| --- | --- | --- |
| 1 | `RecommendationFinalized` | The local adapter supplies an empty finalized-history list, so this algorithm row passes. Record its provenance as **empty local-adapter assumption; real CloudFix history not queried**. Do not describe it as a database check or substitute real history into this comparison algorithm. |
| 2 | `ResourceExists` | The parsed volume map contains the selected resource ID. Failure stops subsequent resource checks. |
| 3 | `ResourceFixed` | Pass when `VolumeType !== 'gp3'`. Already-gp3 fails this row and stops subsequent checks. The name means “not already fixed” when it passes. |
| 4 | `VolumeExpectedType` | `VolumeType === 'gp2'`. Failure stops subsequent checks. |
| 5 | `VolumeAgeXDays` | `differenceInDays(now, CreateTime) >= volumeAgeThreshold`. Equality passes. Missing `CreateTime` fails even at threshold zero, with `create-time-unreported`. Use date-fns completed-day semantics with the adapter's forced UTC timezone; do not round a partial day upward. Do not use the harness host timezone. For these UTC dates, completed-day arithmetic is `Math.trunc((asOfMillis - createTimeMillis) / 86400000)`, including truncation toward zero for future dates; missing creation time remains a failed age check. |
| 6 | `EMRClusterAssociation` or `Ec2InstanceDescribable` | Use the first attachment. No `InstanceId`: EMR check passes. An ID exists but its instance is absent from the completed parsed instance response: emit `Ec2InstanceDescribable=false` (Not Enough Data), in place of the EMR row. Otherwise the presence of tag key `aws:elasticmapreduce:instance-group-role` fails EMR, even if its value is empty; absence passes. |
| 7 | `ElasticVolumeEnabled` | Apply the exact decision tree below. |
| 8 | `MinimalSavings` | `annualSavings > minimalSavings` — strictly greater. Equality and zero at default threshold fail. |
| 9 | `MaximumSavings` | `annualSavings <= annualCost`. Equality passes. |

Validation `type` is `Resource Exists` for ResourceExists, `Resource Fixed` for ResourceFixed, `Savings Threshold` for MinimalSavings/MaximumSavings, `Not Enough Data` for Ec2InstanceDescribable, and `Other` for the remaining rows. Use these exact `value` shapes in the comparison ledger, omitting only undefined properties and preserving explicit null. Keep explanatory provenance outside `value`.

| Check | `value` properties |
| --- | --- |
| RecommendationFinalized, ResourceExists | `{resourceId}` |
| ResourceFixed | `{volumeType, fixedVolumeTypes:["gp3"]}` |
| VolumeExpectedType | `{volumeType, expectedTypes:["gp2"]}` |
| VolumeAgeXDays | `{createTime, ageDays, minAgeDays, reason}`; reason is undefined unless creation time is missing, then `"create-time-unreported"` |
| EMRClusterAssociation | `{attachedInstanceId, emrInstanceGroupRole}`; the latter is the matching tag's Value, including an explicit empty string |
| Ec2InstanceDescribable | `{attachedInstanceId}` |
| ElasticVolumeEnabled | `{createTime, elasticSupportDate}` for a recent creation or no attachment; add `attachTime` when an attachment is inspected; add `instanceLaunchTime` only when the launch-time branch is reached |
| MinimalSavings | `{annualSavings, annualCost, minimalSavings}` |
| MaximumSavings | `{annualSavings, annualCost}` |

The exact elastic-volume cutoff is **2016-12-03T23:40:00.000Z**. In this order:

1. `CreateTime` strictly after cutoff → pass.
2. Otherwise, no first attachment → pass.
3. Otherwise, first attachment `AttachTime` strictly after cutoff → pass.
4. Otherwise, that attached instance's `LaunchTime` must be present and strictly after cutoff.

Equality is not “after.” Missing creation or attachment time substitutes the cutoff for that comparison; it does not become a newer date. The separate age check still rejects missing creation time.

## Control flow and limits

`ResourceExists`, `ResourceFixed` and `VolumeExpectedType` failures set `approved=false` and `stopProcessing=true`. They skip **every subsequent per-resource validation, including `MinimalSavings` and `MaximumSavings`**. List those later checks as skipped, not failed or passed. No sizing, cost or percentage calculation runs; annual savings remains initialized to zero.

Age, EMR/instance-describability and elastic failures set `approved=false` without setting `stopProcessing`. Later resource checks still run. The source finalizes validation before generating parameters, so these rejected resources also retain initialized zero savings, but **their two savings gates do run**. The minimum-savings check can therefore fail too. Do not describe initialized zero as measured absence of potential savings. In `assessment.json` these two checks are `not_evaluated` (see `assessment-guide.md`).

A resource rejected by a savings gate after calculation retains its computed savings and report data, including a negative saving or a saving equal to the minimum. The framework's `clearReportDataOnRejection` defaults to false and gp2 does not override it. Fixer parameters are removed on final rejection, and a rendered recommendation report is gated on approval. Preserve the computed evidence and failed reasons; do not zero out a rejected calculation.

`MinimumResourceCost`, `IsValidState` and `VolumeAttached` exist elsewhere in the framework but are **not invoked by this gp2 validator**. `State` is required for parsing, but this validator does not require `in-use`. It does not enforce a running instance, root/data distinction, ASG membership, CloudWatch utilization, OS/filesystem compatibility, encryption status, Multi-Attach, active-modification history, snapshot availability or application performance. Do not claim those were tested or add them as finder rejection rules. The separate fixer has its own procedure.

**Exact per-item exception contract.** A fault caught inside a wrapped validation callback uses `context:"Validation error: "`. A fault caught inside parameter population uses `context:"Error while populating parameters: "`. Both literal strings end with one space; retain it. The row is `{name:"Exception",type:"Other",approved:false,value:<serialized {context,error,errorName,stack}>}` and sets the item `approved:false,stopProcessing:true`. For an actual Error, `error` is its message and `errorName` its name; for a thrown non-Error, `error=String(thrownValue)`, `errorName:null`, `stack:null`. Preserve the observed stack when available under the native serialization limits, otherwise null; never invent a stack or infer an unrecorded error class. Preserve full diagnostics separately. Only an evidenced thrown fault at the stated boundary produces this row: a missing unattempted read or an ordinary failed validation is not an Exception. Later source stages retain their existing skip, savings and cleanup rules.

The assessment ledger may store this value parsed; it remains a harness assessment, not native finder JSON. DescribeVolumes/DescribeInstances collection and uncaught pricing failures are outside these per-item wrappers; retain the distinct batch/error behavior already specified.

## Proposed settings: copy the sizing calculation exactly

Only produce actionable target settings for resources passing the applicable resource checks. The following is copied verbatim from `calculateProvisionedParameters`; `volumeSizeGib` is the source `Size`.

```ts
const gp2Iops = Math.min(16000, Math.max(100, 3 * volumeSizeGib));
const gp3Iops = Math.min(80000, Math.max(gp2Iops, 3000));
const provisionedIops = Math.max(0, gp2Iops - 3000);
const gp2Throughput = Math.ceil(Math.min(250, gp2Iops / 4));
const gp3Throughput = Math.min(2000, Math.max(gp2Throughput, 125));
const provisionedThroughput = Math.max(0, gp3Throughput - 125);
```

Use a calculator or existing local execution tool with JavaScript-compatible numeric and `toFixed` semantics for the arithmetic. Keep all six outputs and the calculation trace; do not substitute a remembered AWS sizing rule. Throughput is MiB/s. Size is unchanged. The source target class is `gp3`, with `resourceId`, `gp3Iops` and `gp3Throughput`. Monitoring tenant/account identifiers, recommendation IDs and SNS ARNs belong to the CloudFix pipeline; do not invent them for a customer harness.

Live `public_prices`/`billing` collection first requires a finite positive GP2 Size to establish its baseline. Supplied replay can reach the finder with missing/zero Size; do not add the live-only guard to replay. Missing Size skips parameter population in the source. Missing/zero/falsy report size skips the savings calculation. The sizing helper's ability to calculate 3,000/125 for zero does not mean the full finder approves a zero-sized resource. Report the missing/invalid input and do not invent a cost or usable conversion proposal.

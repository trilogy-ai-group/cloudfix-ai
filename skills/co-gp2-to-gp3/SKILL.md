---
name: co-gp2-to-gp3
description: Assess AWS EBS gp2-to-gp3 opportunities locally, derive billing-based estimates from Cost Explorer, or replay evidence. When requested, prepare an infrastructure PR or direct AWS procedure and carry out an authorized change.
---

# Assess GP2 volumes

## 1. Confirm the scope and access

Use the requested AWS account, region and optional volume IDs. Use existing AWS authentication and the customer's chosen AWS profile, if any. No skill profile file is required. Do not ask for credentials in chat or save them in evidence.

The supplied executable is `scripts/gp2.cjs` relative to this skill directory. It requires **Node 22+**. Run it directly; do not rebuild it or write replacement collectors, checks or calculations.

Live resource assessment uses STS `GetCallerIdentity`, EC2 `DescribeVolumes`/`DescribeInstances`, and Pricing `GetProducts`. It needs no CloudWatch metrics, GitHub access or service registration. Optional billing-rate collection uses STS and Cost Explorer `GetDimensionValues`/`GetCostAndUsage` through the customer's existing billing access, which may be in a different payer account/profile. It needs no CUR table. CE must already be enabled and visible to that identity; do not enable paid data settings or alter billing permissions. These API calls incur AWS Cost Explorer request charges. No data goes back to the tool's publisher. Replay makes no AWS calls.

## 2. Run the tool

Choose a new output directory and create its parent first. For a live assessment using AWS public prices:

```sh
node <skill-dir>/scripts/gp2.cjs assess --profile <existing-profile> --region <requested-region> --account <expected-account-id> --cost-basis public --out <new-output-directory>
```

Add `--volume-id <volume-id>` for each selected volume. Without a selection, the tool discovers GP2 volumes in that region. Do not expand to other accounts or regions.

Omit `--profile` to use the default AWS credential chain. Use `--account` whenever the expected account is known; a mismatch stops the run. If the account was unknown, confirm the returned identity before interpreting the assessment.

For billing-based estimates without CUR, run `billing` after the initial resource assessment:

```sh
node <skill-dir>/scripts/gp2.cjs billing --assessment <initial-output-directory> --profile <existing-billing-profile> --out <new-billing-output-directory>
```

Omit `--profile` for the customer's default credential chain. Use existing billing access; the resource profile alone may not expose costs. The tool queries CE for GP2 storage usage in the original account and resource region, using the finder's 31-day UTC window excluding today and yesterday. It discovers and verifies usage units, sums matching net storage cost and usage, and derives the effective monthly rate in code. It then estimates each selected volume's annual cost from its current GiB size. The agent must not calculate or edit these amounts.

**This is an account-and-region effective-rate estimate, not an observed per-volume bill.** Applying the rate assumes comparable GP2 pricing across those volumes. It cannot reconstruct individual billing adjustments, past size changes or resource lifetimes. CE-visible rates may also depend on payer discount visibility or Billing Conductor configuration. Preserve the returned estimate label, source, period, estimated-data flags and any net-to-unblended fallback.

Read `billing-context.json` on success or failure. A successful run also writes `costs.json`, including complete billing evidence. Missing access, empty data, unknown or incompatible units, and unusable costs are not zero costs. Do not silently substitute public prices. If billing-based costs were requested, obtain the missing access or evidence rather than presenting the public-price run as completion.

Rerun `assess` into a new output directory using the original workload account, region and profile, **the exact selected volume IDs**, and `--costs <billing-output-directory>/costs.json` instead of `--cost-basis public`. Read the new assessment and retain both runs. The unchanged finder estimates GP3 using the current public GP3-to-GP2 price ratio. This preserves the original finder calculation, but the CE effective-rate input is different from its historical per-volume CUR baseline; neither establishes a negotiated future GP3 rate or realized savings.

For costs supplied through another verified source, the same `--costs <file.json>` interface accepts:

```json
{
  "currency": "USD",
  "source": "Source of the supplied cost evidence",
  "period": "Observation and annualization period",
  "resources": [
    {"resourceId": "vol-example", "annualCost": 36, "publicAnnualCost": 36}
  ]
}
```

The numbers are examples. Preserve supplied values; every selected resource needs a cost record. Manually supplying costs does not verify their billing provenance.

For recorded evidence or a simulation:

```sh
node <skill-dir>/scripts/gp2.cjs replay --evidence <evidence-file.json> --out <new-output-directory>
```

Replay accepts raw evidence or an `{evidence, run_context}` packet. Preserve supplied configuration and the original input. Use `--help`, `assess --help`, `billing --help` or `replay --help` for command details and `--version` for the package's public build identifier.

## 3. Read the result

The command returns paths to `summary.json`, `result.json`, `evidence.json` and `context.json`. **Read the complete summary**, including every resource, failed check, pricing qualification and outstanding evidence request.

- `summary.json` is generated by code for interpretation. Use its decisions, exact settings, cost basis and execution status.
- `result.json` preserves the complete native assessment unchanged.
- `evidence.json` and `context.json` retain inputs, read results, configuration and provenance. Read the complete relevant records when checking a finding or preparing a change; the summary's artifact references must remain retrievable.

Pricing lookup counts and fallback status are in `cost_basis`. Full queries, units and raw prices are at `cost_basis.lookup_evidence`: resolve its `artifact` through `summary.artifacts`, open that file, and read the complete array at its `json_pointer`. Follow this reference whenever a fallback occurred, provenance is unknown, or pricing details matter to the decision. Do not treat an unavailable evidence file as verified pricing.

For a billing-rate run, also read `context.json` at `/cost_input/billing_evidence`: the requests, source, period, complete returned records, usage units, rate calculations and qualifications remain there through saved replay. A public-price lookup succeeding does not verify the separate CE effective-rate estimate. Keep both price bases and their evidence distinct.

The native report is retained for traceability. Its wording about actual costs, downtime, performance and rollback is unverified; use the summary's cost basis and limitations when explaining the result.

Exit **0** means assessment completed, including rejected candidates. Exit **2** means evidence is missing. Exit **1** means the run failed. Read saved artifacts on every outcome. Return the exact requested operation/parameters or full relevant error; do not turn an access failure into an empty response.

The live tool retrieves required follow-up evidence itself. For replay, obtain the missing evidence and rerun with the complete amended packet into a new directory. Do not repair an earlier output.

## 4. Explain the assessment

Report each resource's decision, failed checks, proposed GP3 settings when approved, estimated annual savings and pricing basis. Use `estimate.display` for dollar amounts shown to the customer; exact numeric amounts remain available for comparisons. Use GiB for size, IOPS and MiB/s for throughput. Preserve negative savings. A saving marked `not_calculated` is unknown, not zero potential.

Use the code-calculated `totals` for aggregate costs and savings. Report its status and included, excluded and unknown resource counts, and use `totals.display` for money. Do not recompute totals or sum rounded per-resource amounts. Totals cover approved candidates with calculated savings; public-price and supplied-cost figures remain separate. A partial or unverified result must not be described as a complete account total.

Use each resource's `evidence_context` for recorded volume, attachment and instance states; absent state is unknown. A stopped instance does not prove that its volume is unused, and EBS storage remains billable while the instance is stopped.

Public prices and supplied costs are estimates unless independently verified. Identify any pricing fallback. Online modification depends on instance support; this assessment does not check instance-type compatibility or measure application performance. Do not promise equivalent-or-better performance, zero impact or immediate rollback.

Keep assessment, proposed change, execution and observed savings separate. Return the artifact paths. An assessment-only request ends here.

## 5. Prepare the appropriate change when requested

Read the saved assessment's complete native result, evidence and context. Identify the workload owner and what manages the selected volume using tags, stack/launch configuration, provisioning code and owner-supplied facts. Missing tags or an unsuccessful repository search do not prove direct management.

| Management established by evidence | Change path |
| --- | --- |
| Code manages the existing volume | Prepare a scoped patch or draft PR; establish whether applying it modifies or replaces the volume. |
| Code controls only future launches | Explain that effect; prepare a separate direct-change procedure for the existing disk when requested. |
| Direct management is confirmed | Prepare a resource-specific AWS change procedure. A PR is not required. |
| Management or ownership is unknown | Prepare a conditional procedure and name the unresolved facts or authorization. Do not invent a repository or label the volume unmanaged. |

Use the exact returned GP3 IOPS and throughput. Preserve size, encryption/KMS, attachment, deletion policy and unrelated environments. Keep any separately requested alternative distinct from the assessed recommendation and its estimate.

**For a repository change:** resolve the repository, branch and environment from evidence; inspect its instructions, local changes and existing PRs. Trace how configuration reaches the workload and check equivalent writers and consumers. Validate with appropriate tests or an offline plan. Inspect commands, hooks and CI first: some builds publish images or deploy infrastructure. A provisioning-default change does not convert existing disks. Honor a local-only or no-PR request; otherwise reuse a relevant draft when appropriate. Verify the published URL, base/head, diff and draft status. If publication is blocked, return the local patch and blocker.

**For a direct change:** prepare the following using [AWS modification requirements](https://docs.aws.amazon.com/ebs/latest/userguide/modify-volume-requirements.html), [volume modification guidance](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modify-volume.html), and [monitoring guidance](https://docs.aws.amazon.com/ebs/latest/userguide/monitoring-volume-modifications.html):

- Record the account, region, volume, attached instance and device; current and proposed settings; estimate and price basis.
- Check current resource and modification state, ownership, infrastructure management, instance support, workload performance requirements and recovery readiness. Identify an appropriate recoverable backup or an owner-approved recovery plan; do not infer readiness from the finder approval. Check applicable AWS modification limits and prerequisites.
- Compare the proposed target with the workload's requirements and [documented GP2 performance](https://docs.aws.amazon.com/ebs/latest/userguide/general-purpose.html). For example, GP2 volumes up to 170 GiB have a documented maximum of 128 MiB/s, while the finder may propose 125 MiB/s. Disclose and resolve performance acceptance without silently changing settings. A stopped instance does not prove a volume is unused.
- Provide the exact command with the selected profile/credential chain, region, volume and returned settings: `aws ec2 modify-volume --region <region> --volume-id <volume-id> --volume-type gp3 --iops <returned-iops> --throughput <returned-throughput>`. Add `--profile <profile>` if applicable; omit `--size` when retaining capacity. The bundled tool never modifies volumes; the customer's harness performs any authorized volume change using its normal AWS tools.
- State execution authorization and unmet conditions. Existing explicit authorization for the concrete operation counts. Assessment, procedure or PR preparation alone does not authorize AWS writes, deployment or merging. Do not request approval again for an already authorized operation with unchanged scope and satisfied conditions.
- Provide monitoring, workload verification and recovery steps. A submitted modification cannot be cancelled. A later modification or recovery action has its own prerequisites and authorization; do not promise immediate rollback.

Before an authorized execution, reread the resource and modification state. If it already matches the target, verify without another write. If a matching modification is active, monitor it. After a timeout or ambiguous submission, inspect state and modification history before retrying. Report conflicting changes or unmet prerequisites explicitly.

## 6. Return the proposal or verified result with evidence

Return the requested patch/draft PR, direct AWS procedure, or both. Include management evidence, exact settings, existing-versus-future effect, estimated savings and price basis, actual validation results, and every unresolved execution/recovery condition. Provide complete material evidence through an authorized private location and verify reviewer access. Local file links alone are not GitHub attachments. Never publish account-wide evidence publicly.

Keep the assessment files unchanged. Record any execution separately with its request, response, resource identity, actual settings, verification and outstanding conditions. A prepared procedure or PR is a proposal: execution remains `not_executed`. Distinguish submission attempted with outcome unknown, submitted, in progress, failed and completed; `optimizing` does not mean completed. After execution, verify actual volume settings and relevant workload checks. Savings remain estimates until billing evidence demonstrates realized savings.

# Preparing a change proposal

Read this only when the user asks how to apply the change, or asks for a patch, pull request or AWS procedure. It never authorizes or performs an AWS change; the assessment files stay unchanged.

The fixer is separate TypeScript/runbook work: it snapshots, waits, changes the volume, checks results and handles cleanup/notifications. This skill does not execute that runbook. For requested implementation, use the harness workflow below; do not infer authorization to run `ModifyVolume`, snapshots or cleanup from a successful assessment.

## Prepare the appropriate change when requested

Read the complete saved skill assessment, raw evidence and calculation records. Identify the workload owner and what manages the selected volume using tags, stack/launch configuration, provisioning code and owner-supplied facts. Missing tags or an unsuccessful repository search do not prove direct management.

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
- Provide the exact command with the selected profile/credential chain, region, volume and returned settings: `aws ec2 modify-volume --region <region> --volume-id <volume-id> --volume-type gp3 --iops <returned-iops> --throughput <returned-throughput>`. Add `--profile <profile>` if applicable; omit `--size` when retaining capacity. This skill performs no change and contains no fixer executable; the customer performs any authorized change with their own AWS tools.
- State the authorization the change needs and its unmet conditions. Assessment, procedure or PR preparation does not authorize AWS writes, deployment or merging. This skill does not execute the change even when the customer has authorized it; the customer's executor acts on that authorization.
- Provide monitoring, workload verification and recovery steps. A submitted modification cannot be cancelled. A later modification or recovery action has its own prerequisites and authorization; do not promise immediate rollback.

Include these steps for whoever executes the change. Before executing, reread the resource and modification state. If it already matches the target, verify without another write. If a matching modification is active, monitor it. After a timeout or ambiguous submission, inspect state and modification history before retrying. Report conflicting changes or unmet prerequisites explicitly.

## Return the proposal or verified result with evidence

Return the requested patch/draft PR, direct AWS procedure, or both. Include management evidence, exact settings, existing-versus-future effect, estimated savings and price basis, actual validation results, and every unresolved execution/recovery condition. Provide complete material evidence through an authorized private location and verify reviewer access. Local file links alone are not GitHub attachments. Never publish account-wide evidence publicly.

Keep the assessment files unchanged. If the customer reports an execution, record it separately with its request, response, resource identity, actual settings, verification and outstanding conditions. A prepared procedure or PR is a proposal: execution remains `not_executed`. Distinguish submission attempted with outcome unknown, submitted, in progress, failed and completed; `optimizing` does not mean completed. After execution, verify actual volume settings and relevant workload checks. Savings remain estimates until billing evidence demonstrates realized savings.

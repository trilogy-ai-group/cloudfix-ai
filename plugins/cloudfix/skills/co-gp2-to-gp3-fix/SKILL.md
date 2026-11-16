---
name: co-gp2-to-gp3-fix
description: Prepare and carry out an authorized GP2-to-GP3 change for EBS volumes from a saved assessment, either directly in AWS with a plan, approval, resumable apply and optional snapshot cleanup, or as a pull request through the co-iac-pr worker when code manages the volume. Use after co-gp2-to-gp3 when the user asks to convert, change, fix or open a PR for assessed volumes.
---

# Change GP2 volumes to GP3

This skill turns a completed assessment (a run directory written by `co-gp2-to-gp3`) into either a pull request for the code that manages a volume, or a direct AWS change that the user has approved. It runs the `cloudfix` command-line tool and explains what it saved. Never ask for credentials in chat or save them anywhere; the AWS profile name is the only credential-related value recorded. The tool writes only `cloudfix-ai:*` tags, never `cloudfix:*`.

The input is a completed assessment run directory. If the user has none, run the `co-gp2-to-gp3` skill first. An approved assessment is not authorization to change anything.

## 1. Command form and start

Every `cloudfix` command in this skill runs as:

```sh
uv run --frozen --no-dev --project "<plugin>/python" cloudfix <args>
```

`<plugin>` is the plugin root: two directories up from this skill's own directory (Claude Code also substitutes `${CLAUDE_PLUGIN_ROOT}`). In the rest of this skill, `cloudfix ...` means that full command. Pass every path as an absolute path in double quotes, as the commands below show, because paths often contain spaces; inside quotes the shell does not expand `~`, so write the home directory out. The first command builds the tool's environment and takes a few seconds; later ones start immediately.

- If `uv --version` fails, stop and say uv is missing. Give the install options for the user's OS: the Astral installer (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS or Linux, `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows), Homebrew (`brew install uv`), WinGet (`winget install --id=astral-sh.uv -e`) or pipx (`pipx install uv`). Install only if the user asks you to.
- If uv reports that the plugin folder is not writable, rerun the same command with `UV_PROJECT_ENVIRONMENT` set to a per-user cache directory: `~/.cache/cloudfix-ai/env` on macOS and Linux, `%LOCALAPPDATA%\cloudfix-ai\env` on Windows. Use the shell's own syntax to set the variable for that command (`UV_PROJECT_ENVIRONMENT=~/.cache/cloudfix-ai/env uv run ...` in sh or bash, `$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\cloudfix-ai\env"` before the command in PowerShell, `set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\cloudfix-ai\env"` in cmd). Keep using it for every later command.
- Any other uv failure (no network, blocked package index): report uv's own message and stop.

Start with:

```sh
cloudfix --version
```

Report any error line verbatim and stop. Every command prints exactly one JSON line; an error is `{"status":"error","error":{"code":...,"message":...}}`. Report it verbatim. Never retry a failed write on your own.

Read the assessment's `summary.json` first. Only volumes it approved can be changed. Report money only from `display` values and do no arithmetic yourself.

## 2. Route each volume

**A volume that any infrastructure-as-code tool manages never gets a direct change.** Terraform, OpenTofu, CloudFormation, CDK, SAM, Pulumi, Kubernetes manifests and any other IaC all count. A direct change would drift from the code, and the next apply could switch the volume back.

A volume counts as IaC-managed when any of these holds:

- it carries a tag the tool treats as evidence. The tool's complete list is:
  - `aws:cloudformation:stack-name` (CloudFormation, and tools built on it: CDK, SAM, Serverless Framework);
  - any tag whose key starts with `pulumi:`;
  - a `ManagedBy`, `managed-by` or `managed_by` tag (any capitalization) whose value names one of terraform, opentofu, pulumi, cloudformation, cdk, crossplane, kubernetes or ansible;
  - a tag whose key is `terraform` (any capitalization);
  - the EBS CSI driver's `kubernetes.io/created-for/pvc/name` or `ebs.csi.aws.com/cluster` (a Kubernetes StorageClass owns the volume);
- the user says code manages it;
- the PR worker finds the volume, or the resource that creates it, in a repository.

Show the user which tag matched. `plan` states it in the refusal reason, and `change.json` carries it as `iac_evidence`. Other tags, such as `Environment` or `Owner`, are not evidence by themselves.

| Evidence | Path |
| --- | --- |
| IaC-managed (any signal above) | Code-managed path only. `plan` refuses a direct change, with no exception. |
| No signal | Ask the user whether code manages the volume and where. |
| User says it is not managed by code | Direct path with `--management not-managed`. The answer is recorded in the plan. |
| User does not know | `plan` may run with `--management unknown`, but `apply` then needs the user's explicit confirmation of direct management. |

Ask about every volume that has no signal, in one question that lists them, before planning.

One case is not IaC management of the volume itself: code that only controls future launches (a launch template or Auto Scaling group whose instances were launched from it) and does not track the existing volume. There the PR worker updates the template for future launches, and you may offer a direct change for the existing volume, because nothing in the code will revert it. Only the PR worker's report can establish this case, and the report must also show that nothing replaces running instances when the template changes: no Auto Scaling `instance_refresh` (Terraform), no `AutoScalingRollingUpdate` or `AutoScalingReplacingUpdate` update policy (CloudFormation), and no managed node group pinned to the template version. If the worker reports that a template change replaces running instances, the change is not future-launches-only. A volume the code tracks directly (an `aws_ebs_volume`, an `aws_instance` `root_block_device`, an `AWS::EC2::Volume`, a Kubernetes StorageClass-provisioned PVC) is IaC-managed and the exception does not apply. If the report does not clearly establish it, treat the volume as IaC-managed.

Volumes in one assessment can take different paths. Handle each group on its own path and report each volume separately.

## 3. Code-managed path

Write the change description for the PR worker:

```sh
cloudfix gp2-to-gp3-fix change --assessment "<run-dir>" --out "<change.json>"
```

Add `--volume-id <id>` (repeatable) to limit the volumes, `--repo-hint <path-or-url>` when the user named a repository, and `--delivery local_patch` when the user wants a patch instead of a draft PR. The default delivery is `draft_pr`. `--out` must not exist yet; put it next to the assessment run directory, outside any repository. If the user wants other IOPS or throughput than the assessment proposed, add `--iops <vol=N>` or `--throughput <vol=N>`; `change.json` then marks the volume `deviates_from_assessment` with both the assessed and the chosen values, and the PR worker uses the chosen `target`. For each volume `change.json` also carries what finds it in code: its `name` (the `Name` tag), its `tags`, and its `attachment` (instance ID, device and instance name, from the assessment's records).

Then hand `change.json` to the `co-iac-pr` skill. It finds the repository, edits, validates offline, reviews and delivers; it follows the change note `iac.md` next to this skill. A draft PR is a proposal, not a change to AWS.

If no repository is found for an IaC-managed volume, stop. Report the evidence you have (stack name, matching tag, or the user's answer) and say that no change was made. Never invent a repository and never fall back to a direct change for that volume.

Report the worker's effect statement as given: in place, replacement, or future launches only, and that without a `terraform plan` it is "per provider documentation, not verified by a plan".

## 4. Direct path

**Choose the AWS profile before planning.** Ask the user which AWS profile the change should use; the default is the assessment's profile (its `config.json`, or the default credential chain when it named none). A read-only profile that was enough for the assessment cannot apply a change. State the permissions the chosen profile needs: `plan` and `status` only read (`sts:GetCallerIdentity`, `ec2:DescribeVolumes`, `ec2:DescribeVolumesModifications`, `pricing:GetProducts`); `apply` also needs `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyVolume` and `ec2:DescribeSnapshots`; `cleanup` needs `sts:GetCallerIdentity`, `ec2:DescribeSnapshots` and `ec2:DeleteSnapshot`.

Write the plan:

```sh
cloudfix gp2-to-gp3-fix plan --assessment "<run-dir>" --management not-managed --volume-id <id> --profile <profile-name> --out "<new-fix-dir>"
```

`--profile <name>` names the profile the user chose; leave it out to act as the assessment's profile. The plan records the profile, so its hash and the approval cover it, and `apply`, `status` and `cleanup` act as that profile. `--volume-id <id>` is required and repeatable: name exactly the volumes on the direct path. `--management` stamps one management basis on every volume in the plan, so make one plan per answer: `--management not-managed` only for volumes the user said code does not manage, and `--management unknown` (a separate plan) for volumes the user does not know about. Never include a volume the user or the PR worker tied to code, even if it carries no IaC tag, and never put "unknown" volumes in a `not-managed` plan: that would skip the confirmation `apply` requires for them. Each plan has its own hash, approval and `apply`. `--iops <vol=N>` and `--throughput <vol=N>` override a volume's target (for example 128 MiB/s to match GP2 burst on a volume of 170 GiB or less); code validates them against GP3 limits and recomputes the estimate. `--out` must be a new directory; put it next to the assessment run directory, outside any repository. The plan carries an expiry, 24 hours after it was made.

`plan` exits 0 with `{"status":"planned","plan_file":...,"sha256":...,"volumes":[...],"refused":[...]}` when at least one volume is planned, and 1 with `"status":"refused"` when none is. A volume can be refused because it carries `cloudfix:dontFixIt`, is IaC-managed, is no longer GP2, changed size since the assessment, has a modification in progress, or was last modified less than 6 hours ago. Report every refusal with its reason; a refused volume is not planned and not a failure.

Read the complete `plan.json` and show it to the user. Show:

- the account, region and `caller_identity` the plan will act as, and the `profile`;
- per volume: the volume ID, attached instance and device, current and target type, size, IOPS and throughput, and `deviates_from_assessment` with both the assessed and the chosen values when set;
- the estimate from `estimate.display`, and the `cost_basis` (public prices or supplied costs, and any fallback);
- the plan's totals over all planned volumes, from `totals`: the summed estimate from `totals.display` and the summed snapshot upper bound from `totals.snapshot_cost_display`. Never add per-volume numbers yourself;
- the recovery snapshot: it is always taken, and `snapshot_cost_display` is a code-computed upper bound on its monthly storage cost; the actual cost is usually lower because only written blocks are stored. The snapshot is kept until `cleanup` and is not deleted automatically;
- the management basis for each volume;
- the exact steps from each volume's `steps`;
- every entry in `refused`, with its reason;
- the plan's SHA-256 and expiry.

Always disclose these performance points. A GP2 volume of 170 GiB or less can burst to 128 MiB/s, while the proposed GP3 throughput may be 125 MiB/s, so peak throughput can be lower than before; the user may ask for a different target. The tool does not check that the instance type supports online modification and does not measure application performance. Do not promise equivalent or better performance, no impact or immediate rollback. The modification can take a while to optimize, and the volume stays online.

## 5. Approval

`apply` needs an approval that covers this plan. Either of two kinds counts.

- **`after_plan`**: the user says yes to the plan you showed.
- **`prior`**: the user already authorized the concrete operation before the plan existed, naming the volumes or a scope such as "convert all approved volumes in this assessment". Proceed to `apply` without asking again only when all of these hold: every planned volume is inside the authorized scope; no volume deviates from the assessed settings (no IOPS or throughput override); every volume's management basis is `not_managed_by_code`, as answered by the user (a plan with `unknown` still needs its explicit confirmation); and the plan has no other unmet condition (nothing refused that the user did not know about, the account and region are the ones they named). If any of these fails, show the plan and ask. Still show the user the plan summary before applying, even when you do not need to ask.

Preparing an assessment, a plan or a PR is never an approval by itself. Silence, enthusiasm for the assessment, or a request to "look at" the volumes is not approval. If the plan expires or is regenerated, the earlier approval of the old plan does not carry over: a new plan has a new hash and needs the approval checked again.

Pass the plan's printed SHA-256 as `--approve`, the kind as `--approval-kind`, and the user's exact words as `--approval-text`. Code refuses a `prior` approval when the plan contains an override or a management basis other than not managed; you judge whether the user's wording covers the planned volumes. For volumes planned with `--management unknown`, ask the user to confirm that they are managed directly and pass `--confirm-direct-management` only after they say so.

## 6. Apply and resume

```sh
cloudfix gp2-to-gp3-fix apply --plan "<plan.json>" --approve <sha256> --approval-kind after_plan --approval-text '<exact approval words>'
```

Use `--approval-kind prior` for an earlier approval, and add `--confirm-direct-management` only after the user confirmed it. Quote `--approval-text` with single quotes in sh, bash or zsh, so `$`, backticks and `!` in the user's words stay literal, and write a single quote inside the words as `'\''`. In PowerShell also use single quotes, and double any single quote inside the words (`''`). In cmd, use double quotes.

`--wait <minutes>` is how long one run waits for completion; the default is 1 minute. Keep `--wait` well below your shell tool's command timeout (Claude Code's default is 2 minutes and its maximum 10; Codex also limits how long a command runs). A longer wait gains nothing: rerunning the same command continues where the last run stopped, while a run killed by a timeout prints nothing. The command sends each AWS write once (reads are retried by the AWS SDK), records everything in `execution.json` next to the plan, and prints the state of every volume.

Exit codes:

- **0**: every volume is `verified`.
- **3**: in progress. At least one volume is not finished. Report each volume's state, then run the same command again (the `--approve`, kind and text unchanged), right away or after a pause; it reconciles with AWS and continues without repeating a write. Keep rerunning on exit 3 until the exit code is 0 or 1. Do not make a new plan.
- **1**: a volume failed, or the command reported an error. Report the `reason` of each failed volume and the error line verbatim. A failed volume is terminal: a rerun does not retry it. Exit 1 does not mean the other volumes stopped: if the output shows volumes still in progress, rerun the same `apply` command to continue them (it is safe: nothing is written twice) until none is in progress, and only then report the outcome. An error before `execution.json` exists (hash mismatch, expired plan, wrong account, refused approval) changed nothing. An error after `execution.json` exists may follow writes: run `status`, then rerun the same `apply`.

**If `apply` was killed or printed nothing** (for example the shell tool timed out), run `status`, then rerun the same `apply` command. Never start a second `apply` for a plan while one is still running.

Volume states, in order: `planned`, `snapshot_requested`, `snapshot_completed`, `modification_submitted`, `optimizing`, `completed`, `verified`; `failed` is the other end. `optimizing` is in progress, not done: the new settings are set but AWS is still optimizing the volume. Only `verified` means the actual type, IOPS and throughput were read back and match the target, and the `cloudfix-ai:*` tags were written.

Check on a plan at any time, without writing anything:

```sh
cloudfix gp2-to-gp3-fix status --plan "<plan.json>"
```

It first checks that the credentials belong to the plan's account, then reports per volume the recorded state, snapshot ID, live type, IOPS and throughput, and the latest modification state and message.

Report each volume separately. One volume's failure does not undo or block the others, and a run can end with some volumes verified and others failed or in progress. For a failed volume, give its state history and reason from `execution.json`. If a modification was submitted but the volume failed later, say that the change may be partly in effect and give the live settings from `status`.

**Failure at the snapshot stage.** If a volume failed because `ec2.CreateSnapshot` errored, read the error code in its `reason`. `UnauthorizedOperation` or `AccessDenied` means AWS refused the request and no snapshot was created: the profile lacks `ec2:CreateSnapshot` (or `ec2:CreateTags`, which tagging the snapshot on creation needs). Tell the user to fix the permissions or choose another profile, then make a new plan. For any other error, a snapshot may still have been created even though none was found by tag: tell the user to look for snapshots tagged `cloudfix-ai:planId=<plan_id>` (the plan ID is in `plan.json`) in the plan's account and region. Never retry `apply` for that volume without a new plan.

**Failure at the modification stage.** If a volume failed because `ec2.ModifyVolume` errored and no matching modification was found, the change may still be in progress on AWS: a modification can take a moment to appear. Tell the user so, and run `status` later to check the live settings and the latest modification before making any new plan for that volume.

A plan that has expired cannot start a new run, but an `execution.json` that already exists can still be resumed with the same command. A changed plan file fails the hash check; do not edit `plan.json`.

## 7. Cleanup

The recovery snapshot is kept until the user decides otherwise; the tool never deletes it on its own. Offer cleanup only after a volume is `verified`, and tell the user that deleting the snapshot removes the recovery point. Cleanup needs its own approval, separate from the apply approval. `cleanup` has no way to pick volumes: it deletes the snapshot of every volume that is `verified` and has a recorded snapshot at the moment it runs, so the approval must cover all of them. Run `status` immediately before showing the deletion list and again immediately before `cleanup`, with no `apply` in between, and show the snapshot ID of each `verified` volume (a volume that was already at the target before any snapshot has none). If the set changed between the two, show the list again and ask again. If the user wants to delete only some of the snapshots, this command cannot do that: say so, and leave the snapshots for the user to delete themselves.

```sh
cloudfix gp2-to-gp3-fix cleanup --plan "<plan.json>" --approve <sha256> --approval-kind after_plan --approval-text '<exact cleanup approval words>'
```

The approval is either `after_plan` (the user said yes after you showed what will be deleted) or `prior` (the user earlier authorized this cleanup by name, for example "delete the snapshots once they are verified"). An approval to apply is not an approval to clean up. The command deletes only the snapshot of a volume whose state is `verified`, which was actually taken, and which carries this plan's `cloudfix-ai:planId` tag, re-read from AWS before deleting. The output lists `deleted` volumes and `skipped` volumes with reasons; report both. Each run is recorded in `execution.json` under `cleanup` with its approval, deleted and skipped lists; a rerun adds a record and keeps the earlier ones.

## 8. Report

Return the requested patch or draft PR, direct AWS procedure, or both. Include management evidence, exact settings, existing-versus-future effect, estimated savings and price basis, actual validation results, and every unresolved execution or recovery condition. Provide complete material evidence through an authorized private location and verify reviewer access. Local file links alone are not GitHub attachments. Never publish account-wide evidence publicly, and never put run artifacts or account data into a repository.

Keep the assessment files unchanged. Record any execution separately: `execution.json` holds each request, response, resource identity, actual settings, verification and outstanding conditions. A prepared plan, patch or PR is a proposal: the execution outcome remains `not_executed` until `apply` has run. Distinguish submission attempted with outcome unknown, submitted, in progress, failed and completed; `optimizing` does not mean completed. After execution, check the actual volume settings with `status` and the relevant workload checks. Savings remain estimates until billing evidence demonstrates realized savings; report `estimate.display` as an estimate and never as a realized saving.

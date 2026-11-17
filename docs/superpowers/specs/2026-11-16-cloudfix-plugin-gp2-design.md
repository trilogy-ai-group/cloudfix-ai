# CloudFix plugin: GP2→GP3 finder, fixer and shared PR worker

Date: 2026-11-16
Status: draft for approval

## Why

Today this repository ships one skill, `skills/co-gp2-to-gp3`, with one executable, `scripts/gp2.cjs`. That file is 8.4 MB. Almost all of it is the CloudFix backend (Sequelize, Inversify, X-Ray, log4js, AWS SDK v3) bundled so that the production finder class can run unchanged with its database, S3 and SNS dependencies stubbed out. The GP2 decision logic itself is about 230 lines (`cloudfix-aws/cloudfix-ff/src/ff/Ebs/Retype/Gp2Volumes/finder/ValidatorService.ts`) plus about 200 lines of shared EBS checks and the pricing lookup.

Two problems follow:

1. **Nobody can review or trust it.** A customer or tester cannot read what the tool does to their account.
2. **Nothing is reusable.** Preparing an infrastructure PR, running a review, and making a direct AWS change are all written as prose inside one SKILL.md. The next finder would copy that prose.

This design replaces the bundle with a small Python code base run through uv, and splits the work into a finder skill, a fixer skill with code that makes the change, and a generic PR worker that any fixer can hand an infrastructure-code change to. Everything ships as one plugin that both Claude Code and Codex can install from this repository.

## Goals

- GP2→GP3 works end to end on the new layout: assess, explain, plan, apply (direct change) or hand off to the PR worker (code-managed volume).
- Every script is small enough to read in one sitting. Dependencies come only from a committed lock file.
- A bootstrap procedure prepares the Python environment with uv before any script runs.
- The finder skill asks the user for its settings instead of relying on silent defaults.
- The PR worker contains no finder-specific knowledge. A fixer supplies that knowledge as a change note.
- One repository serves both the Claude Code and the Codex plugin marketplaces.
- The layout leaves an obvious place for the next finder, without porting one now.

## Non-goals

- Porting any finder other than GP2→GP3.
- Keeping the Python finder in sync with, or proving parity against, the production finder in `cloudfix-aws`. That is a separate, later topic. The Python code is the source of truth for this plugin.
- CloudWatch metrics, CUR or billing integration, a hosted service, or any data sent to CloudFix.
- Installing uv on the user's machine automatically.
- Applying or deploying infrastructure code. The PR worker stops at a draft PR or a local patch.

## Anti-goals (failure even if it technically works)

- Another opaque artifact: no vendored, minified or bundled code; no dependency outside `uv.lock`.
- The fixer writing to AWS without an explicit, recorded approval that covers the plan being applied.
- A direct change to a volume that any infrastructure-as-code tool manages (it would drift, and the next apply could switch it back).
- The agent doing savings arithmetic. Code computes every amount and total; the agent reports them.
- Presenting an estimate, a prepared plan, a submitted modification or an opened PR as realized savings.

## Constraints

- Must run in both Claude Code and Codex from the same skill files.
- Codex plugins cannot ship subagent definitions; Claude Code plugins can (`agents/`). Anything both harnesses need must therefore be a skill.
- The repository is private; installs use the user's existing GitHub access.
- AWS access uses the user's existing credential chain or named profile. Credentials are never asked for in chat, logged, or written to output.
- Assessment stays read-only: STS `GetCallerIdentity`, EC2 `DescribeVolumes` and `DescribeInstances`, Pricing `GetProducts` (us-east-1). Only the fixer's `apply` and `cleanup` write.
- Run artifacts never go into this repository. Test fixtures are synthetic.
- Supported platforms: anywhere uv runs, so macOS, Linux and Windows (native or WSL), in Claude Code and Codex. claude.ai and Cowork are not targets.

## Approaches considered

### Keeping Python honest against production

- Parity fixtures run through both implementations in CI.
- Python as the only source of truth, sync handled later. **Chosen.** The user wants a maintained group of finder skills first; sync is its own topic.

### What the fixer's code does

- **A. The script makes the change** (plan, then approved apply). **Chosen.** Deterministic, reviewable, mirrors the production SSM runbook (`Gp2Volumes/fixer/runbook.yaml`).
- B. The script only writes a plan; the agent runs AWS CLI commands. Keeps the code read-only, but execution varies by harness and model.

### PR worker shape

- **A. One generic worker plus a change note per fixer.** **Chosen.** Repo discovery, branching, validation, review and PR creation are written once; a new finder adds one markdown file.
- B. A specialist worker per fixer. Deeper tailoring, but duplicates the shared flow in every finder.

### Packaging for both harnesses

- **A. One `cloudfix` plugin listed in both marketplace files.** **Chosen.** One environment, one shared library, and the code people review is the code that runs.
- B. A core plugin plus one plugin per finder. Neither harness guarantees a stable path from one plugin to another's install folder.
- C. A Python package installed with `uv tool install git+…`. Skill and tool versions drift independently, uv needs GitHub credentials for the private repo, and the running code lives outside the reviewed plugin folder.

### Decided during design review

- **Direct changes and IaC.** No volume managed by any infrastructure-as-code tool gets a direct change, not only CloudFormation-managed ones.
- **Platforms.** uv makes the Python side platform-neutral, so there is no shell wrapper and every OS uv supports is a target.
- **Tag namespace.** A: the fixer writes its own `cloudfix-ai:*` tags. B, the production `cloudfix:*` tags, was rejected because hosted CloudFix would credit, and could bill, the change as its own saving.
- **Earlier approval.** A: an approval given before the plan exists counts when the plan matches it. B, always stopping after `plan` for a second yes, was rejected as needlessly strict for an already-authorized operation.

## Design

### Repository layout

```text
.claude-plugin/marketplace.json        # Claude Code marketplace (Codex also reads it)
.agents/plugins/marketplace.json       # Codex marketplace
plugins/cloudfix/
  .claude-plugin/plugin.json
  .codex-plugin/plugin.json
  agents/iac-pr-worker.md              # Claude-only: edit side of co-iac-pr
  agents/iac-pr-reviewer.md            # Claude-only: review side of co-iac-pr
  skills/
    co-gp2-to-gp3/SKILL.md             # finder: bootstrap, interview, run, explain
    co-gp2-to-gp3-fix/SKILL.md         # fixer: route, plan, approve, apply, cleanup
    co-gp2-to-gp3-fix/iac.md           # change note for the PR worker
    co-iac-pr/SKILL.md                 # generic PR worker
    co-iac-pr/review.md                # reviewer brief
  python/pyproject.toml
  python/uv.lock
  python/cloudfix/
    __main__.py                        # CLI entry
    common/                            # AWS reads with recording, pricing, money, output files, config
    finders/gp2_to_gp3.py
    fixers/gp2_to_gp3.py
  python/tests/
docs/superpowers/specs/                # this document
README.md
```

The existing `skills/co-gp2-to-gp3/` folder, including `gp2.cjs`, is deleted. (The untracked `.claude/` and `.skills/` copies in the maintainer's working tree are not part of the repository and are left alone.)

There is no shell wrapper and no `bin/` folder. Every command a skill runs goes through uv, which behaves the same on macOS, Linux and Windows.

### Bootstrap procedure

**The command form.** Every skill runs the CLI as:

```text
uv run --frozen --no-dev --project <plugin>/python cloudfix <args>
```

`cloudfix` is a console-script entry point declared in `pyproject.toml`. `<plugin>` is the plugin root, two levels up from the skill's own directory (both harnesses tell a skill its directory; Claude Code also substitutes `${CLAUDE_PLUGIN_ROOT}` in skill text). In the rest of this document, `cloudfix …` means that full command.

**Why that is the bootstrap.** Before running, `uv run` creates the project environment if it is missing and syncs it to `uv.lock` if it is stale. `--frozen` installs exactly what the committed lock says and never re-resolves; `--no-dev` leaves out test tools. uv also downloads a suitable Python if none is installed (`requires-python` in `pyproject.toml`; my call: `>=3.11`), and it serialises concurrent syncs itself. No hand-written bootstrap script is needed, and nothing in the procedure depends on the operating system.

**When it runs.** Neither harness has an event that fires when a plugin is installed or updated, so nothing can run "on install". The environment is built on the first `cloudfix` command instead, and rebuilt on the first command after an update that changes `uv.lock`. That first command takes a few seconds to fetch boto3; later ones start immediately. Each skill's first step is `cloudfix --version`, so a missing uv or a failed sync surfaces before the interview, not halfway through an assessment. The behaviour is identical in Claude Code and Codex, and no hook needs trusting.

Two alternatives were considered and rejected:

- A `SessionStart` hook that warms the environment. Both harnesses let a plugin ship `hooks/hooks.json`, but the hook would run at every session start in every project, download packages the user never asked for, and need a one-time trust approval in Codex. It also cannot install uv, so the first-use check is still needed.
- A separate setup skill the user runs once. Users forget it, and an update that changes `uv.lock` would need it run again; the first-use check is still needed.

**Where the environment lives.** uv's default, `<plugin>/python/.venv`. Each plugin version gets its own install folder, so an update starts with a fresh environment and the old one is removed with the old folder. A manual install keeps `.venv` beside the code; `.gitignore` excludes it. If the plugin folder is not writable, the skill sets `UV_PROJECT_ENVIRONMENT` to a per-user cache directory for that command.

**Missing uv.** The skill checks with `uv --version`. If uv is missing, it stops and gives the official install options for the user's OS: the Astral installer (`curl …/install.sh | sh` on macOS/Linux, `irm …/install.ps1 | iex` in PowerShell), Homebrew, WinGet or pipx. The agent runs an install only if the user asks it to.

**Failures.** The CLI prints one JSON line on any error (`{"status":"error","error":{...}}`) so the skill can report it verbatim. A uv sync failure (no network, blocked package index) is reported with uv's own message.

**Portable code.** The Python code uses `pathlib` and no shell-outs. File permissions (0700 run directory, 0600 files) are applied where the OS supports POSIX modes; on Windows the files inherit the user profile's permissions, and the README says to keep run directories inside the user's profile.

Runtime dependency: `boto3` only. Development dependencies (tests): `pytest`. Nothing else.

### Finder skill: `co-gp2-to-gp3`

The skill:

1. Checks for uv and builds the environment by running `cloudfix --version`.
2. Gets the finder's settings description from code: `cloudfix gp2-to-gp3 describe` prints each setting as JSON (name, question, type, default, required, allowed values). Settings live in code, so the interview pattern is the same for every future finder.
3. Interviews the user only for settings the request did not answer, one message at a time, offering defaults rather than applying them silently:

   | Setting | Default | Notes |
   | --- | --- | --- |
   | AWS account ID | none (strongly recommended) | A mismatch with the caller identity stops the run. |
   | Region | none (required) | Never expanded to other regions. |
   | AWS profile | default credential chain | |
   | Volume IDs | all GP2 volumes in the region | |
   | Cost basis | public prices | Or a supplied-costs file (same JSON shape as today: `currency`=USD, `source`, `period`, `resources[{resourceId, annualCost, publicAnnualCost}]`). |
   | Minimum volume age (days) | 7 | 0 means any age; unknown age always fails. |
   | Minimum annual savings (USD) | 0 | Savings must be strictly greater. |

4. Writes the answers to `config.json` in a new run directory and runs `cloudfix gp2-to-gp3 assess --config <file> --out <new dir>`.
5. Reads the complete `summary.json` and explains it, keeping today's reporting rules: per-volume decision, failed checks, proposed settings, estimates from `display` fields, code-computed totals with their status and counts, pricing fallbacks, the limitations list, and the separation of assessment / proposed change / execution / observed savings. An assessment-only request ends here; otherwise it hands over to the fixer skill.

`replay --evidence <file> --out <dir>` stays for recorded evidence and tests; it makes no AWS calls.

### Finder script: `finders/gp2_to_gp3.py`

**Collection (live).** Same reads as today: `GetCallerIdentity`, `DescribeVolumes` (by ID in batches of 200, or `volume-type=gp2` discovery, with pagination), `DescribeInstances` for attached instances, Pricing `GetProducts`. Every request and response is recorded in `context.json` with timestamps, and the evidence needed for replay goes in `evidence.json`.

**Checks, in order.** The first four fail fast (later checks are not run for that volume). The rest are all evaluated and recorded; the volume is approved only if every recorded check passes.

1. `ResourceExists`: the volume was returned.
2. `DontFixItTag`: fails if the volume carries a `cloudfix:dontFixIt` tag with a non-empty value. This is the opt-out tag CloudFix tells customers to use; production excludes such volumes at discovery (`WhereDoNotFixIt` in `cloudfix-ff/src/common/finders/cur/where.ts`). The summary reports these volumes as excluded by the owner's opt-out.
3. `ResourceFixed`: fails if the volume is already GP3.
4. `VolumeExpectedType`: the volume is GP2.
5. `VolumeAgeXDays`: age in whole days ≥ threshold; a missing `CreateTime` fails at every threshold.
6. `EMRClusterAssociation`: the first attached instance has no `aws:elasticmapreduce:instance-group-role` tag. Unattached passes. If the attached instance was not returned by `DescribeInstances`, record `Ec2InstanceDescribable` as failed instead.
7. `ElasticVolumeEnabled`: passes if the volume was created after 2016-12-03T23:40Z, else if its attachment time is after that date, else if unattached, else if the instance launch time is after that date.
8. `MinimalSavings`: annual savings > the configured minimum.
9. `MaximumSavings`: annual savings ≤ current annual cost.

The production `RecommendationFinalized` check (a CloudFix database lookup) is dropped; there is no history locally.

**Sizing.** For size S GiB:
`gp2Iops = min(16000, max(100, 3·S))`, `gp3Iops = min(80000, max(gp2Iops, 3000))`, `gp2Throughput = ceil(min(250, gp2Iops/4))`, `gp3Throughput = min(2000, max(gp2Throughput, 125))`. Proposed settings are GP3 with size unchanged and those IOPS and throughput.

**Pricing.** Look up per-region GP2 and GP3 storage price (GB-month), GP3 IOPS price above the 3,000 free, and GP3 throughput price above the 125 MiB/s free, through the Pricing API in us-east-1. If a lookup returns nothing, use a recorded default and mark `fallback_used` on that lookup. Annual price for a type is `12 × (S·storage + max(0, iops−freeIops)·iopsPrice + max(0, throughput−freeThroughput)·throughputPrice)`. The reference implementation is `AwsPricingService.getEbsVolumePricing`, `EBS_VOLUME_PRICING_DEFAULTS` and `AwsEbsService.getDiscountedVolumePrice` in `cloudfix-aws`; read them for the exact filters and defaults.

**Savings.** `current = annualCost` (the public GP2 price under the public basis, or the supplied cost). `proposed = current × gp3Public / gp2Public`. `saving = current − proposed`. Public current, proposed and saving are reported separately. Negative savings are kept. Totals sum unrounded amounts over approved volumes with calculated savings, with included, excluded and unknown counts and a status (`calculated`, `partial`, `no_approved_candidates`, `empty`, `unverified`), exactly as today.

**Output.** A new run directory (mode 0700) with files at mode 0600:

- `summary.json`: the shape today's SKILL.md relies on: `status`, `mode`, `assessed_at`, `scope`, `cost_basis` (kind, lookup and fallback counts, `lookup_evidence` pointer), `configuration`, `no_candidates`, `totals`, `resources[]` (decision, checks with details, proposed settings, estimate with `display`, `evidence_context` with volume, attachment and instance state, tags), `limitations`, `execution_outcome: "not_executed"`, `observed_saving: null`, `artifacts`. Fields that only described the bundled production finder (`native_result_pointer`, native report text) are removed.
- `result.json`: the per-volume check ledger and computed figures from the Python finder.
- `evidence.json`, `context.json`: as today, plus `config.json` as answered.

Exit codes: 0 complete (rejections included), 2 needs evidence (replay only; the request is in the summary), 1 error (with the failed reads). Artifacts are written on every outcome. stdout is one JSON line with the status and file paths.

### Fixer skill and script: `co-gp2-to-gp3-fix`, `fixers/gp2_to_gp3.py`

**Routing.** For each approved volume the fixer skill decides between a direct change and the PR worker. The rule: **a volume that any infrastructure-as-code tool manages never gets a direct change.** Terraform, OpenTofu, CloudFormation, CDK, SAM, Pulumi, Kubernetes manifests and any other IaC all count. A direct change would drift from the code, and the next apply could switch the volume back.

A volume counts as IaC-managed when any of these holds:

- it carries `aws:cloudformation:stack-name` (CloudFormation, and tools built on it: CDK, SAM, Serverless Framework);
- it carries a tag that IaC tools or controllers set, such as `pulumi:*`, a `ManagedBy`/`managed-by` or `terraform` tag naming a tool, or the EBS CSI driver's `kubernetes.io/created-for/pvc/name` / `ebs.csi.aws.com/cluster` (a Kubernetes StorageClass owns it). The fixer skill lists the tags it treats as evidence and shows the user which one matched;
- the user says code manages it;
- the PR worker finds the volume, or the resource that creates it, in a repository.

| Evidence | Path |
| --- | --- |
| IaC-managed (any signal above) | PR worker only; `plan` refuses a direct change, with no exception. If the worker finds no repository, the fixer stops and reports the evidence it found (stack name, matching tag, or the user's answer). |
| No signal | Ask the user whether code manages the volume and where. |
| User says it is not managed by code | Direct change; their answer is recorded in the plan. |
| User does not know | `plan` may run, but `apply` requires an explicit confirmation of direct management, recorded in the plan. |

One case is not IaC management of the volume itself: code that only controls future launches (a launch template or Auto Scaling group whose instances were launched from it) and does not track the existing volume. There the PR worker updates the template for future launches, and the fixer may offer a direct change for the existing volume, because nothing in the code will revert it. The worker's report must establish this; a volume the code tracks directly (an `aws_ebs_volume`, an `aws_instance` `root_block_device`, an `AWS::EC2::Volume`) is IaC-managed.

Before planning, the skill states the write permissions needed: `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyVolume`, `ec2:DescribeSnapshots`, `ec2:DescribeVolumesModifications`, plus the read permissions above, and `ec2:DeleteSnapshot` for `cleanup`.

**`plan --assessment <run dir> [--volume-id …] --management <answer> --out <fix dir>`**

- Reads the saved assessment and re-reads each volume and its modification history.
- Refuses a volume that carries `cloudfix:dontFixIt`, is no longer GP2, whose size changed since the assessment, that has a modification in progress, or that was last modified less than 6 hours ago.
- Writes `plan.json`: account, region, per-volume current and target settings, attached instance and device, estimate and price basis, management basis, the exact AWS calls in order, and an expiry (my call: 24 hours).
- **Targets.** By default the targets are the assessment's proposed settings. The user may override IOPS and/or throughput per volume (`--iops`, `--throughput`), for example 128 MiB/s to match GP2 burst on a ≤170 GiB volume. Code validates the override against GP3 limits (including the throughput-to-IOPS ratio), recomputes the estimate from the prices recorded in the assessment, and marks the volume `deviates_from_assessment` with both the assessed and the chosen values. The agent does no arithmetic. `change.json` carries the same marking when an override goes to the PR worker.
- **Recovery snapshot.** Always taken, as in the production runbook. The plan shows a code-computed upper bound on its monthly storage cost (volume size × the region's snapshot storage price from the Pricing API, recorded like the other lookups); the actual cost is usually lower because only written blocks are stored.
- Prints the plan's SHA-256. A plan may list several volumes from one assessment; one approval covers all of them, and each volume progresses and is reported on its own.
- The fixer skill always shows the plan and discloses performance differences from documented GP2 behaviour (for example GP2 ≤170 GiB bursts to 128 MiB/s while the proposal may be 125 MiB/s) and that instance-type support for online modification is not checked.

**Approval.** `apply` needs an approval that covers the plan. Either of two kinds counts:

- **Approval after seeing the plan:** the user says yes to the plan shown.
- **Earlier approval that the plan matches:** the user already authorized the concrete operation before the plan existed, naming the volumes or a scope such as "convert all approved volumes in this assessment". The skill proceeds to `apply` without asking again only when all of these hold: every planned volume is inside the authorized scope; no volume deviates from the assessed settings (no IOPS or throughput override); every volume's management basis is "not managed by code" as answered by the user (a "don't know" still needs its explicit confirmation); and the plan has no other unmet condition. If any fails, the skill shows the plan and asks.

Either way the skill passes the plan's hash to `apply`, together with the approval kind and the user's exact wording. Code enforces the mechanical conditions: `apply` refuses an earlier-approval kind when the plan contains an override or a management basis other than the user's "not managed by code". The agent judges whether the user's wording covers the planned volumes. `execution.json` records the approval kind, wording and timestamp. Preparing an assessment, a plan or a PR is never an approval by itself.

**`apply --plan <plan.json> --approve <sha256> [--wait <minutes>]`**

- Refuses if the hash does not match, the plan has expired, or a volume's live state no longer matches the plan. A volume already at the target is verified without another write.
- Per volume, a resumable sequence recorded in `execution.json` next to the plan: `snapshot_requested` → `snapshot_completed` → `modification_submitted` → `optimizing` → `completed` → `verified`. Each run reconciles with AWS before acting and never sends a write twice.
- Neither `CreateSnapshot` nor `ModifyVolume` takes an idempotency token, so the snapshot is created with tags in the same call (`cloudfix-ai:fixer=gp2-to-gp3`, `cloudfix-ai:originalResourceId`, `cloudfix-ai:executionDate`, `cloudfix-ai:planId`) and a lost response is found again by that `planId` tag. A lost `ModifyVolume` response is resolved through `DescribeVolumesModifications`.
- **Tag namespace.** The fixer writes only `cloudfix-ai:*` tags, never the production `cloudfix:*` keys. Those keys feed CloudFix's hosted savings attribution (`cloudfix-core/src/recommendation/services/SavingsCURQuery.ts` credits resources tagged `cloudfix:fixerId`, `cloudfix:originalResourceId` and `cloudfix:executionDate`) and CloudFix's own test-account snapshot cleanup. A change the user's agent makes must not be credited, or billed, as a hosted CloudFix saving. The plugin still reads the production opt-out tag `cloudfix:dontFixIt`.
- Waits up to `--wait` minutes (default: my call, 15). If not finished, it exits with a distinct "in progress" status; running `apply` again continues. `optimizing` is reported as in progress, not done.
- After completion, verifies the volume's actual type, IOPS and throughput, then tags the volume with the same `cloudfix-ai:*` tags.
- `execution.json` records every request, response, timestamp and final setting. Savings stay estimates.

**`status --plan <plan.json>`** reports the recorded and live state without writing.

**`cleanup --plan <plan.json> --approve <sha256>`** deletes the recovery snapshot only for a volume whose execution is `verified` and whose snapshot carries this plan's `cloudfix-ai:planId`. It needs its own approval (after seeing what will be deleted, or an earlier approval naming the cleanup). This differs from the production runbook, which deletes the snapshot automatically: deleting the recovery point is left to the user.

### PR worker skill: `co-iac-pr`

Input: a `change.json` written by the fixer skill:

```json
{
  "finder": "gp2-to-gp3",
  "account": "123456789012",
  "region": "us-east-1",
  "resources": [{"id": "vol-…", "current": {…}, "target": {…}}],
  "assessment": "<run dir>",
  "change_note": "<path to iac.md>",
  "repo_hint": "<path or URL, optional>",
  "delivery": "draft_pr | local_patch"
}
```

Steps. The `co-iac-pr` skill runs in the main conversation and orchestrates; the editing and the review run in separate subagents where the harness has them, because a Claude Code subagent cannot reliably start another subagent.

1. **Find the repo.** Search, in order: `repo_hint`, the current checkout, and any other local folders the user names, for the resource ID, its name and tags. No GitHub code search or cloning unless the user names the repository. If none is found, report that and stop; never invent one.
2. **Read before editing.** The repo's agent instructions (CLAUDE.md, AGENTS.md), local changes, existing open PRs, hooks and CI configuration (some builds deploy).
3. **Edit and validate** (worker). Branch, edit as the change note describes, and run only offline validators: `terraform fmt -check`, `terraform init -backend=false` then `terraform validate`, `cfn-lint`, and `cdk synth` only where it runs without AWS credentials. No `terraform plan`, apply, deploy or anything that publishes, unless the user asks for a plan and the repo's instructions allow it.
4. **Review** (reviewer). A fresh reviewer, given `review.md`, the diff, the change note and `change.json`, checks the change; the orchestrator sends findings back to the worker, for up to two rounds. In Codex, worker and reviewer are subagents where available, otherwise separate passes.
5. **Deliver.** Push and open a draft PR whose body carries an evidence summary, never account-wide evidence, and return the URL, base, head and diff stat. For `local_patch`, or if publishing is blocked, return the patch and the reason.
6. **Report effect.** State whether the change modifies the existing resource in place, replaces it, or only affects future launches. Without a `terraform plan`, this comes from the change note's per-resource rules and is reported as "per provider documentation, not verified by a plan".

The skill contains no GP2 knowledge. Claude Code gets two thin agent files, `agents/iac-pr-worker.md` (find, edit, validate, commit) and `agents/iac-pr-reviewer.md` (review only, read-only tools), each pointing at the matching section of `co-iac-pr`. Codex runs the same skill without them.

### Change note: `co-gp2-to-gp3-fix/iac.md`

Finder-specific knowledge the worker needs, in plain markdown:

- Terraform: `aws_ebs_volume` `type`, `iops`, `throughput`, which update in place; launch templates and `aws_instance` `root_block_device` or `ebs_block_device` (future launches, or replacement, depending on the resource).
- CloudFormation: `AWS::EC2::Volume` `VolumeType`, `Iops`, `Throughput` (update without interruption); launch templates and block device mappings.
- CDK: `ec2.Volume` `volumeType` and related properties; `BlockDeviceVolume.ebs(...)`.
- What not to change: size, encryption and KMS key, deletion policy, unrelated environments.
- Which validators apply to each tool.

### Marketplaces and manifests

- `.claude-plugin/marketplace.json`: marketplace `cloudfix-ai`, owner, one plugin `cloudfix` with `source: "./plugins/cloudfix"`.
- `.agents/plugins/marketplace.json`: marketplace `cloudfix-ai`, `interface.displayName`, one plugin entry with `source: {"source": "local", "path": "./plugins/cloudfix"}`, `policy.installation: "AVAILABLE"`, `policy.authentication: "ON_INSTALL"`, and `category`.
- `plugins/cloudfix/.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`: name `cloudfix`, the same version, description, author; Codex's adds `skills: "./skills/"` and `interface` metadata.
- Install, documented in the README:
  - Claude Code: `/plugin marketplace add trilogy-ai-group/cloudfix-ai`, then `/plugin install cloudfix@cloudfix-ai`.
  - Codex: `codex plugin marketplace add trilogy-ai-group/cloudfix-ai`, then enable `cloudfix@cloudfix-ai`.
  - Manual: copy the whole `plugins/cloudfix/` folder; a single skill folder does not work on its own.
  - There is no separate setup step: the first command builds the environment (see Bootstrap procedure). The README states the uv prerequisite with install options per OS.
- Check the manifest fields against both vendors' current documentation when building; both formats are recent.

### README

Rewritten around the plugin: what it does, the uv prerequisite, install per harness, access and data (read-only assessment; fixer write permissions), how to read results, and the pilot notes. The package-identity SHA section is replaced by the plugin version (`cloudfix --version`) and the git tag.

### Tests

Under `plugins/cloudfix/python/tests`, run with `uv run pytest`, synthetic data only:

- Sizing at boundary sizes (for example 1, 33, 34, 170, 334, 1000, 5334, 16384 GiB).
- Each check's pass and fail paths through replay fixtures, including the `cloudfix:dontFixIt` tag, missing `CreateTime`, an undescribable instance, an EMR tag and the elastic-support dates.
- Pricing with and without fallbacks; savings and totals including negative and `not_calculated` cases.
- Fixer `plan` refusals, including each IaC-management signal; `apply` refusing an earlier-approval kind for a plan with an override or an unconfirmed management basis; snapshots and volumes carrying only `cloudfix-ai:*` tags; IOPS and throughput overrides (valid, out of range, ratio violation) and their recomputed estimates; the snapshot cost bound; `apply` resumed from every state; a lost `CreateSnapshot` response found by tag; a lost `ModifyVolume` response found through modification history; hash mismatch and expiry; already-at-target. AWS is stubbed with `botocore.stub.Stubber`.
- Environment: a first `uv run --frozen --no-dev` builds it, a second does no work, and a changed lock triggers a sync. `uv lock --check` passes (the committed lock matches `pyproject.toml`). The suite passes on Linux, macOS and Windows.

## Left to the builder

- Exact CLI subcommand and flag spellings, module boundaries inside `common/`, and the JSON field names not fixed above.
- How a skill spells the plugin root in each harness, and when it falls back to `UV_PROJECT_ENVIRONMENT`.
- Exact wording of SKILL.md files, as long as they keep today's reporting rules and the approval rules above.
- Whether to add a GitHub Actions workflow that runs the tests.
- The default values marked "my call" (Python floor, plan expiry, apply wait).

## Calls made without explicit discussion

- Settings are described in code and printed by `describe`, so the interview is the same for every finder.
- `RecommendationFinalized` is dropped.
- The recovery snapshot is kept until `cleanup`, unlike the runbook.
- One approval covers a multi-volume plan; volumes progress independently.
- The skills never install uv. The environment is uv's default `.venv` inside the plugin's `python/` folder, so each plugin version gets a fresh one.
- The environment is built by `uv run --frozen` on first use, not by a `SessionStart` hook, a setup skill or a shell wrapper.
- `cloudfix:dontFixIt` is honoured as a fail-fast check, and `plan` refuses such volumes.
- The user may override IOPS and throughput at plan time; code recomputes the estimate and marks the deviation.
- Code that only controls future launches and does not track the existing volume is not IaC management of that volume; with no repository found for an IaC-managed volume, the fixer stops.
- IaC-management evidence includes the CloudFormation stack tag, tool tags (`pulumi:*`, `ManagedBy`/`terraform`), the EBS CSI driver's Kubernetes tags, the user's answer, and the PR worker finding it in a repository.
- The recovery snapshot is mandatory; the plan shows a code-computed upper bound on its monthly cost.
- The PR worker runs only offline validators, searches only local folders unless the user names a repository, and reports in-place versus replacement from the change note.
- `co-iac-pr` orchestrates from the main conversation; edit and review run in separate subagents.
- Code enforces the mechanical limits on earlier approval (no override, user-confirmed management); the agent judges whether the user's wording covers the planned volumes.
- `cleanup` needs its own approval.
- `result.json` changes meaning, from the production finder's raw output to the Python ledger.

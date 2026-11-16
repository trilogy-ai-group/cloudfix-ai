# CloudFix plugin

AWS cost optimization skills for Claude Code and Codex. **Preview.**

`cloudfix` is a plugin served from the `cloudfix-ai` marketplace in this repository. It runs a local Python tool using your existing AWS access. Customer run data never belongs in this repository.

The plugin sits alongside the standalone [`co-gp2-to-gp3` skill](../../skills/co-gp2-to-gp3/SKILL.md) described in the [repository README](../../README.md). The plugin assesses with public prices or a supplied cost file; Cost Explorer billing estimates are currently available only in the standalone skill. Install one of the two in a harness, not both, so your agent sees a single `co-gp2-to-gp3` skill.

## What the plugin does

The `cloudfix` plugin carries three skills and two helper agents.

- `co-gp2-to-gp3` assesses EBS GP2 volumes for conversion to GP3. The tool gathers evidence, applies the finder's checks and saves a result with a readable summary. It is read-only. It can also replay recorded evidence without AWS calls.
- `co-gp2-to-gp3-fix` plans and applies the conversion for volumes you approve: snapshot, modify, verify, and a later cleanup of the snapshots it made. It writes only after you approve a stored plan.
- `co-iac-pr` prepares an infrastructure-as-code change for a volume that code manages and, only after review, delivers it as a draft PR or as a local patch. The `iac-pr-worker` and `iac-pr-reviewer` agents do that work. It never triggers a deploy.

## Prerequisites

Install [uv](https://docs.astral.sh/uv/). It builds the tool's Python environment on first use and downloads a suitable Python (3.11 or later) if you have none. Pick one option for your system.

- macOS or Linux, official installer: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows, official installer: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- Homebrew: `brew install uv`
- WinGet: `winget install --id=astral-sh.uv -e`
- pipx: `pipx install uv`

You also need AWS credentials from your normal local setup (a profile, SSO or environment variables). The tool never asks for, logs or stores credentials; the profile name is the only credential-related value it records.

## Install

**Claude Code:**

```text
/plugin marketplace add trilogy-ai-group/cloudfix-ai
/plugin install cloudfix@cloudfix-ai
```

**Codex:**

```sh
codex plugin marketplace add trilogy-ai-group/cloudfix-ai
```

Then enable `cloudfix@cloudfix-ai` in Codex's plugin list.

The repository is public, so `marketplace add` needs no GitHub credentials.

**Manual install:** copy the whole `plugins/cloudfix/` folder, which includes its licence, into your harness's plugin location. A single skill folder does not work on its own, because the skills share the tool in `plugins/cloudfix/python/`.

There is no setup step. The first command a skill runs builds the environment from the committed lock file, which takes a few seconds to fetch boto3. Each skill starts by checking `uv` and the tool version, so a missing uv or a failed sync shows up before any assessment begins. If the plugin folder is read-only, the skill points the environment at a per-user cache directory.

Start a fresh conversation and ask, for example:

> Use co-gp2-to-gp3 to assess GP2 volumes in my approved dev/test account ACCOUNT_ID, region REGION, using my existing AWS access. Report the recommendations and supporting evidence. This request is assessment only; do not change resources.

Specify a volume ID for a single-resource assessment. Keep run output outside this checkout, and on Windows inside your user profile. Unless you name a location, the skills put runs in `~/cloudfix-runs` (`%USERPROFILE%\cloudfix-runs` on Windows).

## Access and data

- Assessment reads only. It uses STS `GetCallerIdentity`, EC2 `DescribeVolumes` and `DescribeInstances`, and Pricing `GetProducts`. The requested AWS account and region bound the resource reads; Pricing queries use `us-east-1`.
- The fixer adds `ec2:DescribeVolumesModifications`, `ec2:DescribeSnapshots`, `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyVolume`, and `ec2:DeleteSnapshot` (cleanup only). It needs them only for volumes you approve in a plan, and it runs only after you approve that exact plan. If the profile you assessed with is read-only, the plan can name another profile with these permissions; the skill asks which profile to use.
- The fixer writes only `cloudfix-ai:*` tags: `cloudfix-ai:fixer=gp2-to-gp3`, `cloudfix-ai:originalResourceId`, `cloudfix-ai:executionDate` and `cloudfix-ai:planId`. It deliberately never writes the production `cloudfix:*` tags, because the hosted CloudFix service uses those to attribute savings, and a tag written by this tool would be counted as a hosted CloudFix fix.
- A volume tagged `cloudfix:dontFixIt` is honoured: the finder rejects it, so it is neither assessed as an opportunity nor planned for a fix.
- CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional `co-iac-pr` workflow.
- No CloudFix account, API token or hosted service is required, and nothing is sent to CloudFix. There is no CloudFix fee or model call from the tool. Your own harness may use a model under your existing plan.
- Evidence and results stay in the local output directory. Recorded-evidence replay makes no AWS calls. Run directories are created with mode 0700 and files with 0600 on POSIX systems. Never put credentials or customer run artifacts in this repository or its issues.

## Reading the result

Each live run saves `config.json` (the settings it ran with), `summary.json`, the finder's `result.json`, `evidence.json` and `context.json`; a replay saves all of them except `config.json`. Keep the complete files together. The summary separates the assessment decision, estimated saving, execution status and observed saving; missing evidence and rejected candidates are valid outcomes.

The summary includes code-calculated totals for approved candidates with calculated savings, with included, excluded and unknown resource counts. The code sums unrounded amounts before formatting the total, so the agent does no savings arithmetic and reports the `display` values. Public-price and supplied-cost totals remain separate. Partial or unverified results are labelled. Recorded volume, attachment and instance states are included when available; missing state stays unknown.

The summary shows pricing lookup counts and fallback status. `cost_basis.lookup_evidence` points to the complete lookup records in the referenced `context.json`; read those records when a fallback occurred, provenance is unknown, or pricing details affect a decision.

The result does not verify actual costs, downtime, performance or rollback; use the summary's pricing basis and limitations. A stopped instance does not establish that its EBS volume is unused, and the volume remains billable.

An approved assessment is a recommendation, and assessment never modifies a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires an approved plan and verification. Unknown ownership remains an unresolved condition rather than proof that no code manages the resource. A patch or PR does not establish that a disk changed or that savings were realized.

For preview testing, use a dev/test scope approved by its owner and retain the complete transcript and result files privately. Record installation friction and any human help needed. Share a redacted description of problems in [GitHub issues](https://github.com/trilogy-ai-group/cloudfix-ai/issues), without credentials, account identifiers, resource evidence or billing data.

## Version

The plugin version is the git tag of the release you installed. The tool reports the same version. From a clone of this repository, run:

```sh
uv run --frozen --no-dev --project plugins/cloudfix/python cloudfix --version
```

If you installed the plugin, ask the agent to run `cloudfix --version` through the skill's command form; each skill also does this first.

## Supported platforms

Anywhere uv runs: macOS, Linux and Windows, in Claude Code and Codex. The test suite runs on all three in CI.

## Licence

The plugin is covered by the [CloudFix AI Public Preview License](LICENSE), a copy of the repository's licence kept in the plugin folder so it travels with every install. It is source-available, not open source. Nothing third-party is bundled: uv installs the tool's Python dependencies (boto3 and its dependencies) from PyPI on first use, under their own licences.

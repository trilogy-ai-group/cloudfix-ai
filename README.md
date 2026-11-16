# CloudFix AI

AWS cost optimization skills and tools for your agent. **Public preview: EBS GP2 to GP3.**

Give your agent this repository URL. It can read the skill and fetch the executable without a GitHub login or permanent skill installation. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available skill

[`co-gp2-to-gp3`](skills/co-gp2-to-gp3/SKILL.md) assesses EBS GP2 volumes for conversion to GP3. Your harness runs the bundled tool locally using your existing AWS access. The tool gathers evidence, invokes the original finder code and saves its result with a readable summary. Use `assess --cost-basis billing` to collect resources, derive a GP2 effective rate from Cost Explorer and run the finder in one command. The tool carries the volume selection forward automatically and labels the resulting per-volume estimates. No CUR setup is required. When requested, the skill also guides your harness through an infrastructure patch/PR or a direct AWS change procedure for a volume managed outside code. It distinguishes confirmed direct management from unknown ownership and carries out only explicitly authorized changes.

The skill has two runtime files:

```text
skills/co-gp2-to-gp3/
  SKILL.md
  scripts/gp2.cjs
```

## Plugin preview for Claude Code and Codex

This repository also offers [`cloudfix`](plugins/cloudfix/README.md), an installable plugin for Claude Code and Codex. It carries the same GP2 assessment as a small, reviewable Python tool run through [uv](https://docs.astral.sh/uv/), a fixer that carries out an approved conversion plan itself, and a generic infrastructure-code PR worker. It is an early preview alongside the skill above: it assesses with public prices or a supplied cost file, and Cost Explorer billing estimates remain available only in the skill above for now.

```text
/plugin marketplace add trilogy-ai-group/cloudfix-ai
/plugin install cloudfix@cloudfix-ai
```

In Codex, run `codex plugin marketplace add trilogy-ai-group/cloudfix-ai` and enable `cloudfix@cloudfix-ai`. Install either the plugin or the standalone skill in a harness, not both, so your agent sees a single `co-gp2-to-gp3` skill. The [plugin README](plugins/cloudfix/README.md) covers its prerequisites, access and data, results and changes.

## Try it with your agent

Start a fresh conversation with an agent that can read web pages, download files and run local commands. Give it this instruction, replacing the account and region:

> Read https://github.com/trilogy-ai-group/cloudfix-ai and follow its GP2-to-GP3 skill for AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Assess the opportunities and explain the estimated savings and any limitations. Prepare the appropriate proposed change for review. Do not apply changes or merge a PR.

The agent needs **Node.js 22 or later** and your existing AWS credentials in its execution environment. If it cannot execute code or access AWS, it should explain what is missing. Never paste credentials into a conversation.

For agents:

1. Read [`SKILL.md`](https://raw.githubusercontent.com/trilogy-ai-group/cloudfix-ai/main/skills/co-gp2-to-gp3/SKILL.md), this README, the [preview licence](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.txt).
2. Fetch this public repository into a new local working directory. No GitHub credentials are needed:

   ```sh
   git clone https://github.com/trilogy-ai-group/cloudfix-ai.git
   ```

   Alternatively, download `SKILL.md` and `scripts/gp2.cjs` with their relative layout, keeping the licence and notices alongside the copy. Fetch all files from the same commit. The executable is [available directly](https://raw.githubusercontent.com/trilogy-ai-group/cloudfix-ai/main/skills/co-gp2-to-gp3/scripts/gp2.cjs).
3. Verify the executable against the SHA-256 under [Package identity](#package-identity) before running it. Then follow the skill using the authorized account, region and pricing basis. Do not rebuild it or write replacement collectors or calculations.
4. Keep run output outside the checkout. Report the complete relevant findings and qualifications, with links to private evidence. Assessment and proposal preparation do not authorize AWS writes, deployment or merging.

Specify a volume ID for a single-resource assessment. For assessment only, say so; the skill stops after reporting the result. Missing billing access is reported as an error, with collected resource evidence retained. A public-price run requires a separate explicit choice.

### Optional permanent installation

Your agent can use the fetched files directly. Permanent installation is useful only if you want the harness to discover the skill automatically in later conversations.

From the cloned `cloudfix-ai` directory, choose the appropriate personal location:

   **Claude Code:**

   ```sh
   mkdir -p "$HOME/.claude/skills"
   test ! -e "$HOME/.claude/skills/co-gp2-to-gp3" && cp -R skills/co-gp2-to-gp3 "$HOME/.claude/skills/" && cp LICENSE THIRD_PARTY_NOTICES.txt "$HOME/.claude/skills/co-gp2-to-gp3/"
   ```

   **Codex:**

   ```sh
   mkdir -p "$HOME/.agents/skills"
   test ! -e "$HOME/.agents/skills/co-gp2-to-gp3" && cp -R skills/co-gp2-to-gp3 "$HOME/.agents/skills/" && cp LICENSE THIRD_PARTY_NOTICES.txt "$HOME/.agents/skills/co-gp2-to-gp3/"
   ```

These commands stop if the destination already exists and include the licence and third-party notices in a new copy. For an upgrade, replace only the previous managed skill directory after checking for local changes, so old development files cannot survive an overlay. If you installed the earlier `co-gp2-to-gp3-tool` package, remove that old managed copy after the same check. No product profile file is required.

## Access and data

- Live assessment uses STS `GetCallerIdentity`, EC2 `DescribeVolumes` and `DescribeInstances`, and Pricing `GetProducts`. The requested AWS account and region bound the resource reads; Pricing queries use `us-east-1`.
- The executable does not modify workload resources. CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional patch/PR workflow. Direct changes use your harness's normal AWS tools and require the relevant write permissions and execution authorization; the bundled executable never modifies volumes.
- Optional billing-rate collection uses existing Cost Explorer access (`GetDimensionValues` and `GetCostAndUsage`) and STS identity verification. Your billing profile may belong to a different payer account. CE API requests incur AWS charges; the tool does not enable billing settings, provision infrastructure or require CUR.
- No CloudFix account, CloudFix API token or hosted service is required. There is no CloudFix fee or model call from the bundled executable. Your own harness may use a model under your existing plan.
- The executable stores evidence and results in the local output directory and sends nothing to CloudFix. Live collection calls AWS; downloading this package contacts GitHub. Your chosen harness may send the data it reads to its own model provider under your settings. Recorded-evidence replay makes no AWS calls. Never put credentials or customer run artifacts in this repository or its issues.

## Billing-based estimates without CUR

Run one command with your existing resource access:

```sh
node skills/co-gp2-to-gp3/scripts/gp2.cjs assess \
  --profile <workload-profile> --account <expected-account-id> --region <region> \
  --cost-basis billing --out <new-output-directory>
```

Add `--billing-profile <billing-profile>` when costs require separate payer access. Without it, billing uses the workload profile; without either profile, both use the default AWS credential chain. Add `--volume-id <id>` to select particular volumes, or omit it to discover GP2 volumes in the region. The tool carries the exact selection through every stage; no manual ID list or second assessment command is required.

The command collects resource evidence once, retrieves a GP2 billing rate, then runs the unchanged finder with the generated cost inputs. No GP2 volumes is a successful result with nothing to optimize, and skips billing calls. Missing billing access, empty cost data for existing volumes and unsupported units remain errors.

### Failure and retry

If billing fails, the command exits with an error and retains all collected resource evidence and billing diagnostics. It returns no calculated savings and does not substitute a list-price assessment. Preserve the output directory. Once billing access or data is resolved, retry billing against that snapshot:

```sh
node skills/co-gp2-to-gp3/scripts/gp2.cjs assess --cost-basis billing \
  --resume <failed-output-directory> --billing-profile <billing-profile> \
  --out <new-output-directory>
```

The billing profile override is optional. Resume preserves the original resource scope, observation time, configuration and prior failure evidence; it makes no new resource or pricing reads. Results describe the saved snapshot, so an execution workflow must recheck live resource state. To intentionally obtain public-price savings instead, make a separate `assess --cost-basis public` run. Offline replay of a billing failure remains a failed billing result.

### Rate, permissions and request cost

The tool verifies usage units and computes matching net GP2 storage cost divided by matching GB-month usage in the original account and region. It estimates each selected volume's current annual cost as current GiB × effective monthly rate × 12. Every calculation happens in code. The 31-day UTC window excludes today and yesterday.

This is a **GP2 account-and-region effective-rate estimate**, not observed per-volume billing. It assumes comparable GP2 pricing across the selected volumes and cannot reconstruct individual adjustments, size changes or historical lifetimes. This method is GP2-specific: do not apply one rate to EC2 instances with different reservations or Savings Plans allocations.

The caller needs existing Cost Explorer visibility. An available net-cost field does not prove that all payer discounts are visible. If that field is missing, the tool uses unblended cost and records that qualification; hidden discounts cannot reliably be detected. Billing Conductor can also make the visible rate pro forma. Preserve all returned qualifications and CE estimated-data flags.

[AWS charges $0.01 per Cost Explorer API request using the primary billing view](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/). A successful run without pagination uses two CE requests, approximately **$0.02**. Additional pages and billable retries can increase this. The tool does not enable paid data settings or change permissions.

The original finder estimates GP3 using the public GP3-to-GP2 price ratio. The CE current-capacity baseline differs from CloudFix's historical per-volume CUR baseline; exact agreement is not guaranteed. Neither establishes negotiated future GP3 pricing or realized savings.

### Separate billing and supplied costs

The existing `billing --assessment <completed-assessment-directory> [--profile <billing-profile>] --out <new-directory>` command remains available. It writes `billing-context.json` on success or failure and `costs.json` when a rate is established; an empty assessment is a successful no-op with no cost file. The cost file can be used with `assess --costs <file>` and the same exact account, region and selected IDs.

For a different verified cost source, `--costs` accepts this shape:

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

These amounts are examples. Every selected resource needs a record. Manually supplied values remain unchanged and do not establish their own billing provenance.

## Reading the result

Each assessment or replay saves `summary.json`, the unchanged native `result.json`, `evidence.json` and `context.json`. Keep the complete files together. The summary separates the assessment decision, estimated saving, execution status and observed saving; missing evidence and rejected candidates are valid outcomes.

The summary includes code-calculated totals for approved candidates with calculated savings, with included, excluded and unknown resource counts. The code sums unrounded amounts before formatting the total; the agent does no savings arithmetic. Public-price and supplied-cost totals remain separate. Partial or unverified results are explicitly labelled. Recorded volume, attachment and instance states are included when available; missing state stays unknown.

The summary shows pricing lookup counts and fallback status. `cost_basis.lookup_evidence` points to the complete lookup records in the referenced `context.json`; read those records when a fallback occurred, provenance is unknown, or pricing details affect a decision. Full queries, units, raw prices and read errors remain available.

For billing-rate assessments, complete CE records, units and calculation evidence are also preserved at `context.json` → `cost_input.billing_evidence`, including across saved replay. Read these qualifications when interpreting the estimate.

The native report is retained verbatim for traceability. Its claims about actual costs, downtime, performance and rollback are unverified; use the summary's pricing basis and limitations. A stopped instance does not establish that its EBS volume is unused, and the volume remains billable.

An approved assessment is a recommendation. The executable does not modify a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires an explicitly authorized procedure and verification; existing authorization for the concrete operation counts. A direct-change procedure includes current-state and recovery checks, exact settings, monitoring, and verification. Unknown ownership remains an unresolved condition rather than proof that no code manages the resource.

For preview testing, use a dev/test scope approved by its owner and retain the complete transcript and result files privately. Record discovery or setup friction and any human help needed. Share a redacted description of problems in [GitHub issues](https://github.com/trilogy-ai-group/cloudfix-ai/issues), without credentials, account identifiers, resource evidence or billing data. A patch or PR does not establish that a disk changed or that savings were realized.

## Licence

The [CloudFix AI Public Preview License](LICENSE) permits free use to assess and optimize AWS accounts you own or are authorized to manage. Redistribution and offering the package as a hosted service are reserved to the rights holders. This is a source-available preview, not an open-source licence. Bundled third-party components retain their own licences, listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Package identity

The tested executable's SHA-256 is:

```text
639c435b9c835796d31b635ed7d5c943135030977988b7f8da9c849d7d7d57f9
```

Run `node skills/co-gp2-to-gp3/scripts/gp2.cjs --version` for its package name and public build identifier. That identifier links to the complete private maintainer build record. The package runs directly; it needs no source checkout, dependency installation or rebuild.

To check the downloaded executable on macOS:

```sh
shasum -a 256 skills/co-gp2-to-gp3/scripts/gp2.cjs
```

On Linux, use `sha256sum` with the same path. Compare with the hash above from the same repository revision; stop if it differs.

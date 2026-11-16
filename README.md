# CloudFix AI

AWS cost optimization skills and tools for your agent. **Private pilot.**

This repository holds the packages testers install. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available skill

[`co-gp2-to-gp3`](skills/co-gp2-to-gp3/SKILL.md) assesses EBS GP2 volumes for conversion to GP3. Your harness runs the bundled tool locally using your existing AWS access. The tool gathers evidence, invokes the original finder code and saves its result with a readable summary. Its optional `billing` command derives an account-and-region GP2 effective rate from Cost Explorer and feeds labelled per-volume estimates into the same finder. No CUR setup is required. When requested, the skill also guides your harness through an infrastructure patch/PR or a direct AWS change procedure for a volume managed outside code. It distinguishes confirmed direct management from unknown ownership and carries out only explicitly authorized changes.

The installable folder contains exactly two files:

```text
skills/co-gp2-to-gp3/
  SKILL.md
  scripts/gp2.cjs
```

## Try it

1. Install **Node.js 22 or later** and authenticate to your AWS account using your normal local credentials.
2. Obtain this private repository with your existing GitHub access:

   ```sh
   gh repo clone trilogy-ai-group/cloudfix-ai
   ```

3. From the cloned `cloudfix-ai` directory, choose one of these personal installations:

   **Claude Code:**

   ```sh
   mkdir -p "$HOME/.claude/skills"
   test ! -e "$HOME/.claude/skills/co-gp2-to-gp3" && cp -R skills/co-gp2-to-gp3 "$HOME/.claude/skills/"
   ```

   **Codex:**

   ```sh
   mkdir -p "$HOME/.agents/skills"
   test ! -e "$HOME/.agents/skills/co-gp2-to-gp3" && cp -R skills/co-gp2-to-gp3 "$HOME/.agents/skills/"
   ```

   These commands stop if the destination already exists. For an upgrade, remove the previous managed `co-gp2-to-gp3` skill directory and rerun the chosen commands, so old development files cannot survive an overlay. If you installed the earlier `co-gp2-to-gp3-tool` package, also remove that old installed directory so your harness discovers only one copy. No product profile file is required.
4. Start a fresh conversation with your agent, for example:

   > Use co-gp2-to-gp3 to assess GP2 volumes in my approved dev/test account ACCOUNT_ID, region REGION, using my existing AWS access. Report the recommendations and supporting evidence. This request is assessment only; do not change resources.

Specify a volume ID if you want a single-resource assessment. Follow the skill's instructions for running the executable and reading all saved artifacts. Keep run output outside this checkout.

## Access and data

- Live assessment uses STS `GetCallerIdentity`, EC2 `DescribeVolumes` and `DescribeInstances`, and Pricing `GetProducts`. The requested AWS account and region bound the resource reads; Pricing queries use `us-east-1`.
- The executable does not modify workload resources. CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional patch/PR workflow. Direct changes use your harness's normal AWS tools and require the relevant write permissions and execution authorization; the bundled executable never modifies volumes.
- Optional billing-rate collection uses existing Cost Explorer access (`GetDimensionValues` and `GetCostAndUsage`) and STS identity verification. Your billing profile may belong to a different payer account. CE API requests incur AWS charges; the tool does not enable billing settings, provision infrastructure or require CUR.
- No CloudFix account, CloudFix API token or hosted service is required. There is no CloudFix fee or model call from the bundled executable. Your own harness may use a model under your existing plan.
- Evidence and results remain in the local output directory. Nothing is sent to CloudFix. Recorded-evidence replay makes no AWS calls. Never put credentials or customer run artifacts in this repository or its issues.

## Billing-based estimates without CUR

Start with the resource assessment described in the skill. Then retrieve a GP2 effective rate for that account and region using the same executable:

```sh
node skills/co-gp2-to-gp3/scripts/gp2.cjs billing \
  --assessment <initial-assessment-directory> \
  --profile <existing-billing-profile> --out <new-billing-output-directory>
```

Use existing CE access; the resource profile alone may not expose costs. The command writes complete evidence to `billing-context.json` and, on success, writes `costs.json`. It verifies usage units, divides matching net GP2 storage cost by matching storage usage, and estimates each selected volume's current annual cost from its GiB size. The tool performs the arithmetic; the agent does not.

Rerun `assess` with the original workload profile, account, region and exact selected volume IDs, replacing `--cost-basis public` with `--costs <billing-output-directory>/costs.json`. Empty data, denied access and unsupported units are reported explicitly; they do not become zero costs or an undisclosed public-price fallback.

The result is labelled **CE account-and-region effective-rate estimate**, not observed per-volume billing. It assumes comparable GP2 pricing across the selected volumes and cannot reconstruct their individual billing adjustments, size changes or historical lifetimes. The 31-day UTC window excludes today and yesterday. CE's estimated-data flags, cost basis and payer-visibility qualifications remain with the result.

The original finder still calculates GP3 cost using the public GP3-to-GP2 price ratio. This preserves its calculation, but the effective-rate baseline differs from its historical per-volume CUR baseline. Neither guarantees the future negotiated GP3 rate or establishes realized savings.

## Reading the result

Each assessment or replay saves `summary.json`, the unchanged native `result.json`, `evidence.json` and `context.json`. Keep the complete files together. The summary separates the assessment decision, estimated saving, execution status and observed saving; missing evidence and rejected candidates are valid outcomes.

The summary includes code-calculated totals for approved candidates with calculated savings, with included, excluded and unknown resource counts. The code sums unrounded amounts before formatting the total; the agent does no savings arithmetic. Public-price and supplied-cost totals remain separate. Partial or unverified results are explicitly labelled. Recorded volume, attachment and instance states are included when available; missing state stays unknown.

The summary shows pricing lookup counts and fallback status. `cost_basis.lookup_evidence` points to the complete lookup records in the referenced `context.json`; read those records when a fallback occurred, provenance is unknown, or pricing details affect a decision. Full queries, units, raw prices and read errors remain available.

For billing-rate assessments, complete CE records, units and calculation evidence are also preserved at `context.json` → `cost_input.billing_evidence`, including across saved replay. Read these qualifications when interpreting the estimate.

The native report is retained verbatim for traceability. Its claims about actual costs, downtime, performance and rollback are unverified; use the summary's pricing basis and limitations. A stopped instance does not establish that its EBS volume is unused, and the volume remains billable.

An approved assessment is a recommendation. The executable does not modify a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires an explicitly authorized procedure and verification; existing authorization for the concrete operation counts. A direct-change procedure includes current-state and recovery checks, exact settings, monitoring, and verification. Unknown ownership remains an unresolved condition rather than proof that no code manages the resource.

For the private pilot, record installation friction and any human help needed. Use a dev/test scope approved by its owner, and retain the complete transcript and result files privately. A patch or PR does not establish that a disk changed or that savings were realized.

## Package identity

The tested executable's SHA-256 is:

```text
9af0782341228dfde1c1c26f087ec13433577bfc4139779800efd2c8684aedaf
```

Run `node skills/co-gp2-to-gp3/scripts/gp2.cjs --version` for its package name and public build identifier. That identifier links to the complete private maintainer build record. The package runs directly; it needs no source checkout, dependency installation or rebuild.

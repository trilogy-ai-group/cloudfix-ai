# CloudFix AI

AWS cost optimization skills and tools for your agent. **Private pilot.**

This repository holds the packages testers install. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available skill

[`co-gp2-to-gp3`](skills/co-gp2-to-gp3/SKILL.md) assesses EBS GP2 volumes for conversion to GP3. Your harness runs the bundled tool locally using your existing AWS access. The tool gathers evidence, invokes the original finder code and saves its result with a readable summary. When requested, the skill can also guide your harness through preparing an infrastructure patch or draft PR.

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
- The executable is read-only. CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional patch/PR workflow.
- No CloudFix account, CloudFix API token or hosted service is required. There is no CloudFix fee or model call from the bundled executable. Your own harness may use a model under your existing plan.
- Evidence and results remain in the local output directory. Nothing is sent to CloudFix. Recorded-evidence replay makes no AWS calls. Never put credentials or customer run artifacts in this repository or its issues.

## Reading the result

Each run saves `summary.json`, the unchanged native `result.json`, `evidence.json` and `context.json`. Keep the complete files together. The summary separates the assessment decision, estimated saving, execution status and observed saving; missing evidence and rejected candidates are valid outcomes.

The summary includes code-calculated totals for approved candidates with calculated savings, with included, excluded and unknown resource counts. The code sums unrounded amounts before formatting the total; the agent does no savings arithmetic. Public-price and supplied-cost totals remain separate. Partial or unverified results are explicitly labelled. Recorded volume, attachment and instance states are included when available; missing state stays unknown.

The native report is retained verbatim for traceability. Its claims about actual costs, downtime, performance and rollback are unverified; use the summary's pricing basis and limitations. A stopped instance does not establish that its EBS volume is unused, and the volume remains billable.

An approved assessment is a recommendation. The executable does not modify a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires a separately approved procedure and verification.

For the private pilot, record installation friction and any human help needed. Use a dev/test scope approved by its owner, and retain the complete transcript and result files privately. A patch or PR does not establish that a disk changed or that savings were realized.

## Package identity

The tested executable's SHA-256 is:

```text
5907a99b0dfac5fa9ecb242044a827d34a7d197710327f19dcac5d17d0d526be
```

Run `node skills/co-gp2-to-gp3/scripts/gp2.cjs --version` for its package name and public build identifier. That identifier links to the complete private maintainer build record. The package runs directly; it needs no source checkout, dependency installation or rebuild.

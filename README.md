# CloudFix AI

AWS cost optimization skills and tools for your agent. **Private pilot.**

This repository holds the packages testers install. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available skill

[`co-gp2-to-gp3-tool`](skills/co-gp2-to-gp3-tool/SKILL.md) assesses EBS GP2 volumes for conversion to GP3. Your harness runs the bundled tool locally using your existing AWS access. The tool gathers evidence, invokes the original finder code and saves its result with a readable summary. When requested, the skill can also guide your harness through preparing an infrastructure patch or draft PR.

The installable folder contains exactly two files:

```text
skills/co-gp2-to-gp3-tool/
  SKILL.md
  scripts/gp2.cjs
```

## Try it

1. Install **Node.js 22 or later** and authenticate to your AWS account using your normal local credentials.
2. Obtain this private repository with your existing GitHub access:

   ```sh
   gh repo clone trilogy-ai-group/cloudfix-ai
   ```

3. Install `skills/co-gp2-to-gp3-tool` through your harness's local-skill mechanism, preserving the two-file layout. Common locations are `.claude/skills/co-gp2-to-gp3-tool` and `.agents/skills/co-gp2-to-gp3-tool` inside your project. For an upgrade, replace the previous managed skill directory so old development files cannot survive an overlay. No product profile file is required.
4. Start a fresh conversation with your agent, for example:

   > Use co-gp2-to-gp3-tool to assess GP2 volumes in my approved dev/test account ACCOUNT_ID, region REGION, using my existing AWS access. Report the recommendations and supporting evidence. This request is assessment only; do not change resources.

Specify a volume ID if you want a single-resource assessment. Follow the skill's instructions for running the executable and reading all saved artifacts. Keep run output outside this checkout.

## Access and data

- Live assessment uses STS `GetCallerIdentity`, EC2 `DescribeVolumes` and `DescribeInstances`, and Pricing `GetProducts`. The requested AWS account and region bound the resource reads; Pricing queries use `us-east-1`.
- The executable is read-only. CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional patch/PR workflow.
- No CloudFix account, CloudFix API token or hosted service is required. There is no CloudFix fee or model call from the bundled executable. Your own harness may use a model under your existing plan.
- Evidence and results remain in the local output directory. Nothing is sent to CloudFix. Recorded-evidence replay makes no AWS calls. Never put credentials or customer run artifacts in this repository or its issues.

## Reading the result

Each run saves `summary.json`, the unchanged native `result.json`, `evidence.json` and `context.json`. Keep the complete files together. The summary separates the assessment decision, estimated saving, execution status and observed saving; missing evidence and rejected candidates are valid outcomes.

An approved assessment is a recommendation. The executable does not modify a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires a separately approved procedure and verification.

For the private pilot, record installation friction and any human help needed. Use a dev/test scope approved by its owner, and retain the complete transcript and result files privately. A patch or PR does not establish that a disk changed or that savings were realized.

## Package identity

The tested executable's SHA-256 is:

```text
3482581de661d2ba9fc810cf223cfc952ec8a16ae4a8753067e262d10199f013
```

Run `node skills/co-gp2-to-gp3-tool/scripts/gp2.cjs --version` for build provenance. The package runs directly; it needs no source checkout, dependency installation or rebuild.

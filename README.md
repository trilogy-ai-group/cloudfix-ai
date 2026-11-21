# CloudFix AI

AWS cost optimization skills for your agent. **Public preview: five guided assessments.**

Give your agent this repository URL and name the assessment you want. It can select a full commit SHA and fetch the complete skill package without a GitHub login or permanent installation. Use your existing AWS access; no CloudFix account, service, token or new AWS infrastructure is required. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available guided skills

Each guided package contains `SKILL.md` and its complete `references/` folder. Your agent follows the documented finder rules with its existing AWS tools and calculator; no executable or particular language runtime is bundled. Results include saved evidence, `assessment.json` and a customer report. An assessment does not authorize resource changes.

| Skill | Assessment | Preview qualification |
| --- | --- | --- |
| [GP2 to GP3](skills/co-gp2-to-gp3-guided/SKILL.md) | Identify EBS GP2 conversions and calculate proposed settings | Effective-rate estimates can differ from historical per-volume bills; sizing does not establish application performance. |
| [Unattached EBS volumes](skills/co-ebs-unattached/SKILL.md) | Assess detached volumes for a snapshot, archive and removal proposal | Current cost and modeled archive cost have different evidence; a fallback archive price must remain labelled. |
| [S3 Intelligent-Tiering](skills/co-s3-intelligent-tiering/SKILL.md) | Assess transitions from Standard and Standard-IA storage | Future tier mix and savings are modeled; missing size or billing history can leave an assessment partial. |
| [CloudFront compression](skills/co-cloudfront-compression/SKILL.md) | Identify cache behaviors with compression disabled | Technical recommendations only. No monetary savings are claimed, and a configuration finding does not establish traffic or economic relevance. |
| [QuickSight idle users](skills/co-quicksight-idle-users/SKILL.md) | Assess recorded activity, user eligibility and subscription costs | Activity coverage can be incomplete. An effective subscription rate does not prove the saving from removing one user. |

These are public previews for feedback and iteration. Trial assessments found pricing differences that are still being investigated; independent comparison against AWS MCP is not yet complete. Passing the result schema does not establish the accuracy of the underlying evidence or guarantee savings. Keep every result's qualifications visible. Never treat missing information as zero or add overlapping savings together.

### Try a guided skill

Start a fresh conversation with an agent that can read the package, access your AWS account and run calculations. For example:

> Read https://github.com/trilogy-ai-group/cloudfix-ai and follow its unattached EBS volumes guided skill for AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Assess the opportunities and explain estimated savings and limitations. Use billing estimates if my existing billing access permits; report missing access instead of silently switching to public prices. Prepare any proposed change for review, but do not apply changes or merge a PR.

Replace the skill name, account, region and pricing preference. CloudFront defaults to a technical assessment with no savings claim. Read the chosen skill's access and billing requirements before collection; Cost Explorer requests incur AWS charges. Never paste credentials into a conversation.

For agents:

1. Resolve this repository's selected revision to one full commit SHA. Record it and fetch `README.md`, `LICENSE` and **every file in the chosen `skills/<skill-name>/` folder** from that same commit. Preserve the `references/` hierarchy. Fetching only `SKILL.md` is insufficient.
2. With Git, clone the repository into a new directory, record `git rev-parse HEAD`, and keep that checkout unchanged for the run. With HTTP, enumerate the selected folder at the pinned commit and fetch its complete contents from `https://raw.githubusercontent.com/trilogy-ai-group/cloudfix-ai/<commit-sha>/`. Do not mix files from a moving branch.
3. Read the whole `SKILL.md`, then read each reference at the stage that names it. Use the skill's exact rules and a calculator or existing execution tool for calculations; do not invent or mentally estimate numbers. A guided result is a **skill assessment**, not JSON emitted by the original finder.
4. Save complete evidence, the assessment and the report outside the package checkout. The six stages cover scope/access, collection, analysis, costing, verification and reporting, with the exact order and prerequisites stated in each skill. Incomplete evidence and rejected candidates are valid outcomes.
5. Assessment and proposal preparation do not authorize AWS writes, deployment or merging. Follow the selected skill's change limitations and require explicit authorization for any later execution.

The files can be used directly. Permanent installation is optional; if your harness supports skill discovery, copy the complete selected folder and licence to its skill directory. No product profile is required. Your harness may send data to its model provider under your settings; keep customer evidence and billing data private. Report problems using a redacted description in [GitHub issues](https://github.com/trilogy-ai-group/cloudfix-ai/issues).

## Plugin preview

All six skills are also packaged as [`cloudfix`](plugins/cloudfix/README.md), an installable plugin. In Claude Code:

```text
/plugin marketplace add trilogy-ai-group/cloudfix-ai
/plugin install cloudfix@cloudfix-ai
```

The plugin carries exact copies of the five guided skills above and of the `co-gp2-to-gp3` executable skill below, with their `references/` and `scripts/` folders. Fetching a skill directly, as described above, works the same and needs no installation.

## Existing GP2 executable

The original-finder executable remains available separately. The instructions below, including the Node requirement, output filenames and executable checksum, apply only to `co-gp2-to-gp3`.

[`co-gp2-to-gp3`](skills/co-gp2-to-gp3/SKILL.md) assesses EBS GP2 volumes for conversion to GP3. Your harness runs the bundled tool locally using your existing AWS access. The tool gathers evidence, invokes the original finder code and saves its result with a readable summary. Use `assess --cost-basis billing` to collect resources, derive a GP2 effective rate from Cost Explorer and run the finder in one command. The tool carries the volume selection forward automatically and labels the resulting per-volume estimates. No CUR setup is required. When requested, the skill also guides your harness through an infrastructure patch/PR or a direct AWS change procedure for a volume managed outside code. It distinguishes confirmed direct management from unknown ownership and carries out only explicitly authorized changes.

The skill has two runtime files:

```text
skills/co-gp2-to-gp3/
  SKILL.md
  scripts/gp2.cjs
```

### Try the executable with your agent

Start a fresh conversation with an agent that can read web pages, download files and run local commands. Give it this instruction, replacing the account and region:

> Read https://github.com/trilogy-ai-group/cloudfix-ai and follow its co-gp2-to-gp3 executable skill for AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Assess the opportunities and explain the estimated savings and any limitations. Prepare the appropriate proposed change for review. Do not apply changes or merge a PR.

The agent needs **Node.js 22 or later** and your existing AWS credentials in its execution environment. If it cannot execute code or access AWS, it should explain what is missing. Never paste credentials into a conversation.

For agents:

1. Fetch the repository into a new local working directory and record the exact commit. Keep that checkout unchanged for the run. No GitHub credentials are needed:

   ```sh
   git clone --single-branch https://github.com/trilogy-ai-group/cloudfix-ai.git
   git -C cloudfix-ai rev-parse --verify 'HEAD^{commit}'
   ```

2. Read this README, `skills/co-gp2-to-gp3/SKILL.md` and `LICENSE` from that checked-out commit. **Retain** `THIRD_PARTY_NOTICES.txt` with the package; the agent does not need to read the complete notices to perform an assessment.
3. Verify the executable against the SHA-256 under [Package identity](#package-identity) in the same checked-out README before running it. Record both the commit and executable hash. Follow that commit's skill using the authorized account, region and pricing basis. Do not rebuild it or write replacement collectors or calculations.
4. For HTTP-only fetching, resolve the selected repository revision to its full commit SHA first. Fetch the README, licence, notices, skill and executable from `https://raw.githubusercontent.com/trilogy-ai-group/cloudfix-ai/<commit-sha>/`, preserving the skill's relative layout. Verify using that commit's README. Do not mix versions or run files downloaded from a moving branch.
5. Keep run output outside the checkout. Report the complete relevant findings and qualifications, with links to private evidence. Assessment and proposal preparation do not authorize AWS writes, deployment or merging.

Specify a volume ID for a single-resource assessment. A request for assessment alone ends after the result. A request that also asks how to apply it continues to a proposed change for review; execution still needs explicit authorization. Missing billing access is reported as an error, with collected resource evidence retained. A public-price run requires a separate explicit choice.

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

- State the required actions before running: `sts:GetCallerIdentity`, `ec2:DescribeVolumes`, `ec2:DescribeInstances` when instance evidence is needed, and `pricing:GetProducts`. The requested AWS account and region bound the resource reads; Pricing queries use `us-east-1`.
- Identity/account verification runs first. The tool performs and reuses its Pricing lookups before volume inventory, so denied Pricing access stops before the account's volumes are collected. It performs a scoped EC2 dry-run immediately before a needed volume or instance read; an unattached volume does not require an unrelated instance probe. Early access checks do not guarantee every later scoped request will succeed. Failures identify the action, profile and region and retain earlier evidence; the tool does not change IAM policies.
- The executable does not modify workload resources. CloudWatch metrics and GitHub access are not needed for assessment. Repository access is needed only for the optional patch/PR workflow. Direct changes use your harness's normal AWS tools and require the relevant write permissions and execution authorization; the bundled executable never modifies volumes.
- Optional billing-rate collection uses existing Cost Explorer access (`ce:GetDimensionValues` and `ce:GetCostAndUsage`) and STS identity verification. Your billing profile may belong to a different payer account. Billing mode also needs Pricing access: CE supplies the GP2 effective rate, while the original finder uses the public GP3/GP2 ratio to estimate proposed costs. CE API requests incur AWS charges; the tool does not enable billing settings, provision infrastructure or require CUR.
- No CloudFix account, CloudFix API token or hosted service is required. There is no CloudFix fee or model call from the bundled executable. Your own harness may use a model under your existing plan.
- The executable stores evidence and results in the local output directory and sends nothing to CloudFix. Outputs can contain account IDs, private IP addresses and key names. Keep the complete files private; redact only excerpts shared publicly. Live collection calls AWS; downloading this package contacts GitHub. Your chosen harness may send the data it reads to its own model provider under your settings. Recorded-evidence replay makes no AWS calls. Never put credentials or customer run artifacts in this repository or its issues.

## Billing-based estimates without CUR

Before using CE, state its approximately $0.02 request cost for a successful unpaginated run. If billing estimates were requested or already authorized, proceed without another approval prompt. If no price basis was specified, resolve billing estimates versus public prices once. Then run one command with your existing resource access:

```sh
node skills/co-gp2-to-gp3/scripts/gp2.cjs assess \
  --profile <workload-profile> --account <expected-account-id> --region <region> \
  --cost-basis billing --out <new-output-directory>
```

Add `--billing-profile <billing-profile>` when costs require separate payer access. Without it, billing uses the workload profile; without either profile, both use the default AWS credential chain. Add `--volume-id <id>` to select particular volumes, or omit it to discover GP2 volumes in the region. The tool carries the exact selection through every stage; no manual ID list or second assessment command is required.

The command checks identity and Pricing, collects resource evidence once, retrieves a GP2 billing rate, then runs the unchanged finder with the generated cost inputs. No GP2 volumes is a successful result with nothing to optimize, and skips billing calls. CE has no dry-run: the tool uses the needed billing queries after inventory, without extra paid permission probes. Missing billing access, empty cost data for existing volumes and unsupported units remain errors.

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

The summary and top-level context identify the final assessment price basis. Public-price collection and the original supplied-cost inputs remain separately labelled provenance. A public-price collection stage within a billed run does not make the final assessment a public-price result. Read the final basis together with the complete billing evidence rather than relabelling or discarding the original input.

The summary includes code-calculated totals for approved candidates with calculated savings, with included, excluded and unknown resource counts. The code sums unrounded amounts before formatting the total; the agent does no savings arithmetic. Public-price and supplied-cost totals remain separate. Partial or unverified results are explicitly labelled. Recorded volume, attachment and instance states are included when available; missing state stays unknown.

The summary shows pricing lookup counts and fallback status. `cost_basis.lookup_evidence` points to the complete lookup records in the referenced `context.json`; read those records when a fallback occurred, provenance is unknown, or pricing details affect a decision. Full queries, units, raw prices and read errors remain available.

For billing-rate assessments, complete CE records, units and calculation evidence are also preserved at `context.json` → `cost_input.billing_evidence`, including across saved replay. Read these qualifications when interpreting the estimate.

The native report is retained verbatim for traceability. Its claims about actual costs, downtime, performance and rollback are unverified; use the summary's pricing basis and limitations. A stopped instance does not establish that its EBS volume is unused, and the volume remains billable.

An approved assessment is a recommendation. The executable does not modify a volume. Public-price estimates can differ from billed costs. The finder does not check instance-type support or application performance, and its sizing must not be treated as a universal equivalent-or-better performance guarantee. Execution requires an explicitly authorized procedure and verification; existing authorization for the concrete operation counts. A direct-change procedure includes current-state and recovery checks, exact settings, monitoring, and verification. Unknown ownership remains an unresolved condition rather than proof that no code manages the resource.

For preview testing, use a dev/test scope approved by its owner and retain the complete transcript and result files privately. Record discovery or setup friction and any human help needed. Share a redacted description of problems in [GitHub issues](https://github.com/trilogy-ai-group/cloudfix-ai/issues), without credentials, account identifiers, resource evidence or billing data. A patch or PR does not establish that a disk changed or that savings were realized.

## Controlled experiments and replay

Ordinary replay reproduces recorded evidence without AWS calls. It does not automatically make a run experimental, and a legitimate nondefault configuration does not imply synthetic data. The original finder's normal minimum volume age remains seven days.

If the user explicitly requests a test that changes evidence or a finder rule, work on a new copy of the replay packet, preserve the original files, and label the experiment in the command:

```sh
node skills/co-gp2-to-gp3/scripts/gp2.cjs replay \
  --evidence <copied-test-packet.json> \
  --experiment-reason "Age-rule-disabled replay for testing only" \
  --out <new-test-output-directory>
```

This flag records intent; it does not disable the age rule or change any finder setting. For that particular test, change `volumeAgeThreshold` to `0` only in the copied configuration. Raw evidence supports top-level `configuration`; a saved packet may carry `evidence.configuration` or `run_context.configuration`. If both packet locations are present they must agree. Do not edit the previous run or the tool's output to make the test pass.

`assess`, including billing `--resume`, also accepts `--experiment-reason`. A replay packet can supply the same intent directly:

```json
{
  "reason": "Age-rule-disabled replay for testing only"
}
```

Place this object at `run_context.experiment` in the packet, retaining any additional caller metadata. The tool preserves it through repeated replay, errors and resume. If a command also supplies a reason, it must agree with an inherited reason; disagreement is an error rather than a silent relabelling.

Read the code-produced `run_classification`, `experiment` and `configuration_overrides` in summary and context. The overrides list settings that differ from the normal defaults. Report experimental results as such, with the stated reason and overrides. The native finder JSON remains unchanged. A test-only result with the age rule disabled does not replace the normal assessment, authorize a change or establish realized savings.

## Licence

The [CloudFix AI Public Preview License](LICENSE) permits free use to assess and optimize AWS accounts you own or are authorized to manage. Redistribution and offering the package as a hosted service are reserved to the rights holders. This is a source-available preview, not an open-source licence. Bundled third-party components retain their own licences, listed in [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

## Package identity

The tested executable's SHA-256 is:

```text
358da88dd9cee6aa0b2b61578b90b1cd77ec30a8ad53cb6fe02d101e0abbcc80
```

Run `node skills/co-gp2-to-gp3/scripts/gp2.cjs --version` for its package name and public build identifier. That identifier links to the complete private maintainer build record. The package runs directly; it needs no source checkout, dependency installation or rebuild.

To check the downloaded executable on macOS:

```sh
shasum -a 256 skills/co-gp2-to-gp3/scripts/gp2.cjs
```

On Linux, use `sha256sum` with the same path. Compare with the hash above from the same repository revision; stop if it differs.

The hash verifies the downloaded bytes against the selected commit. Review and release controls govern who can publish a new version. Record the resolved commit and intentionally select a new revision when upgrading; a branch update must not silently change the version used for a run.

For more AWS optimization options, see [CloudFix](https://cloudfix.com/). This local skill requires no CloudFix registration.

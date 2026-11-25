# CloudFix AI

AWS cost optimization skills for your agent. **Public preview: five assessments.**

Give your agent this repository URL and name the assessment you want. It can select a full commit SHA and fetch the complete skill package without a GitHub login or permanent installation. Use your existing AWS access; no CloudFix account, service, token or new AWS infrastructure is required. Implementation source, build tooling, tests and customer run data are maintained separately.

## Available skills

Each package contains `SKILL.md`, `scripts/assess.py` and its complete `references/` folder. Your agent runs the bundled script, which needs Python 3.9 or later and AWS CLI v2, installs nothing and makes read-only AWS calls. If your agent cannot run code, it follows `references/guided.md`: the same documented finder rules with its existing AWS tools and calculator. Results include saved evidence, `assessment.json` and a customer report. An assessment does not authorize resource changes.

| Skill | Assessment | Preview qualification |
| --- | --- | --- |
| [GP2 to GP3](skills/co-gp2-to-gp3/SKILL.md) | Identify EBS GP2 conversions and calculate proposed settings | Effective-rate estimates can differ from historical per-volume bills; sizing does not establish application performance. |
| [Unattached EBS volumes](skills/co-ebs-unattached/SKILL.md) | Assess detached volumes for a snapshot, archive and removal proposal | Current cost and modeled archive cost have different evidence; a fallback archive price must remain labelled. |
| [S3 Intelligent-Tiering](skills/co-s3-intelligent-tiering/SKILL.md) | Assess transitions from Standard and Standard-IA storage | Future tier mix and savings are modeled; missing size or billing history can leave an assessment partial. |
| [CloudFront compression](skills/co-cloudfront-compression/SKILL.md) | Identify cache behaviors with compression disabled | Technical recommendations only. No monetary savings are claimed, and a configuration finding does not establish traffic or economic relevance. |
| [QuickSight idle users](skills/co-quicksight-idle-users/SKILL.md) | Assess recorded activity, user eligibility and subscription costs | Activity coverage can be incomplete. An effective subscription rate does not prove the saving from removing one user. |

These are public previews for feedback and iteration. Trial assessments found pricing differences that are still being investigated; independent comparison against AWS MCP is not yet complete. Passing the result schema does not establish the accuracy of the underlying evidence or guarantee savings. Keep every result's qualifications visible. Never treat missing information as zero or add overlapping savings together.

### Try a skill

Start a fresh conversation with an agent that can read the package, access your AWS account and run local commands or calculations. For example:

> Read https://github.com/trilogy-ai-group/cloudfix-ai and follow its unattached EBS volumes skill for AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Assess the opportunities and explain estimated savings and limitations. Use billing estimates if my existing billing access permits; report missing access instead of silently switching to public prices. Prepare any proposed change for review, but do not apply changes or merge a PR.

Replace the skill name, account, region and pricing preference. CloudFront defaults to a technical assessment with no savings claim. Read the chosen skill's access and billing requirements before collection; Cost Explorer requests incur AWS charges. Never paste credentials into a conversation.

For agents:

1. Resolve this repository's selected revision to one full commit SHA. Record it and fetch `README.md`, `LICENSE` and **every file in the chosen `skills/<skill-name>/` folder** from that same commit. Preserve the `references/` and `scripts/` hierarchy. Fetching only `SKILL.md` is insufficient.
2. With Git, clone the repository into a new directory, record `git rev-parse HEAD`, and keep that checkout unchanged for the run. With HTTP, enumerate the selected folder at the pinned commit and fetch its complete contents from `https://raw.githubusercontent.com/trilogy-ai-group/cloudfix-ai/<commit-sha>/`. Do not mix files from a moving branch.
3. Read the whole `SKILL.md`. Run `scripts/assess.py` as it states; do not rewrite its collection, checks or calculations. If you cannot run code, follow `references/guided.md` and read each reference at the stage that names it. Use the skill's exact rules and a calculator or existing execution tool for calculations; do not invent or mentally estimate numbers. A result is a **skill assessment**, not JSON emitted by the original finder.
4. Save complete evidence, the assessment and the report outside the package checkout. On the guided path, the six stages cover scope/access, collection, analysis, costing, verification and reporting, with the exact order and prerequisites stated in each skill. Incomplete evidence and rejected candidates are valid outcomes.
5. Assessment and proposal preparation do not authorize AWS writes, deployment or merging. Follow the selected skill's change limitations and require explicit authorization for any later execution.

The files can be used directly. Permanent installation is optional; if your harness supports skill discovery, copy the complete selected folder and licence to its skill directory. No product profile is required. Your harness may send data to its model provider under your settings; keep customer evidence and billing data private. Report problems using a redacted description in [GitHub issues](https://github.com/trilogy-ai-group/cloudfix-ai/issues).

### Optional permanent installation

Your agent can use the fetched files directly. Permanent installation is useful only if you want the harness to discover the skill automatically in later conversations.

From the cloned `cloudfix-ai` directory, choose the appropriate personal location and replace `<skill-name>`:

   **Claude Code:**

   ```sh
   mkdir -p "$HOME/.claude/skills"
   test ! -e "$HOME/.claude/skills/<skill-name>" && cp -R skills/<skill-name> "$HOME/.claude/skills/" && cp LICENSE "$HOME/.claude/skills/<skill-name>/"
   ```

   **Codex:**

   ```sh
   mkdir -p "$HOME/.agents/skills"
   test ! -e "$HOME/.agents/skills/<skill-name>" && cp -R skills/<skill-name> "$HOME/.agents/skills/" && cp LICENSE "$HOME/.agents/skills/<skill-name>/"
   ```

These commands stop if the destination already exists and include the licence in a new copy. For an upgrade, replace only the previous managed skill directory after checking for local changes, so old development files cannot survive an overlay. The earlier Node executable (`scripts/gp2.cjs`) and the separate `co-gp2-to-gp3-guided` package are retired: `co-gp2-to-gp3` now holds the script and the guided path. If you installed `co-gp2-to-gp3-guided` or the earlier `co-gp2-to-gp3-tool` package, remove that old managed copy after the same check. No product profile file is required.

## Licence

The [CloudFix AI Public Preview License](LICENSE) permits free use to assess and optimize AWS accounts you own or are authorized to manage. Redistribution and offering the package as a hosted service are reserved to the rights holders. This is a source-available preview, not an open-source licence.

For more AWS optimization options, see [CloudFix](https://cloudfix.com/). This local skill requires no CloudFix registration.

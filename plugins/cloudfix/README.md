# CloudFix plugin

AWS cost optimization skills for your agent. **Preview.**

`cloudfix` is a plugin served from the `cloudfix-ai` marketplace in this repository. It carries five skills.

## What it does

| Skill | Assessment |
| --- | --- |
| `co-gp2-to-gp3-guided` | Identify EBS GP2 conversions and calculate proposed settings |
| `co-ebs-unattached` | Assess detached volumes for a snapshot, archive and removal proposal |
| `co-s3-intelligent-tiering` | Assess transitions from Standard and Standard-IA storage |
| `co-cloudfront-compression` | Identify cache behaviors with compression disabled (technical findings only, no savings claim) |
| `co-quicksight-idle-users` | Assess recorded activity, user eligibility and subscription costs |

All five are guided skills: instruction files with a `references/` folder and no code. Your agent follows them with its own AWS tools and a calculator. Each skill prepares a change only when you ask for one and carries out only what you explicitly authorize. The preview qualifications for each skill are in the [repository README](../../README.md#available-guided-skills); keep them visible with every result.

- **No CloudFix account, token or connection is needed.**
- **Assessment is read-only.** Each skill states the AWS read actions it needs before collection. Cost Explorer requests, where a skill uses them, incur AWS charges.
- **Nothing is sent to CloudFix.** Evidence and results stay with your agent, under your own harness and model settings.

## Install

In Claude Code:

```text
/plugin marketplace add trilogy-ai-group/cloudfix-ai
/plugin install cloudfix@cloudfix-ai
```

Then ask your agent, replacing the skill, account and region:

> Use the co-ebs-unattached skill to assess AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Do not apply changes.

You need AWS credentials from your normal local setup. Never paste credentials into a conversation.

## Same skills, two places

Each skill folder here is an exact copy of the matching folder under [`skills/`](../../skills), which agents can also fetch directly without installing anything. `LICENSE` is an exact copy of the repository's. The plugin carries no code: the `co-gp2-to-gp3` executable skill stays in `skills/` only. A check on every pull request fails if any copy differs, if a guided skill exists in one place and not the other, or if the plugin contains any file other than Markdown and JSON. Edit the original, then copy the whole folder here.

## Licence

See [LICENSE](LICENSE).

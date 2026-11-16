# CloudFix plugin

AWS cost optimization skills for your agent. **Preview.**

`cloudfix` is a plugin served from the `cloudfix-ai` marketplace in this repository. It carries one skill today.

## What it does

`co-gp2-to-gp3-guided` assesses EBS GP2 volumes for conversion to GP3. It is one instruction file with no code. Your agent follows it with its own AWS tools and a calculator, and an independent reviewer where your harness has one. It prepares a change when you ask for one and carries out only what you explicitly authorize.

- **No CloudFix account, token or connection is needed.**
- **Assessment is read-only.** It reads STS, EC2 and AWS Pricing. Billing estimates also read Cost Explorer, at about $0.02 for a successful run.
- **Nothing is sent to CloudFix.** Evidence and results stay with your agent, under your own harness and model settings.

## Install

In Claude Code:

```text
/plugin marketplace add trilogy-ai-group/cloudfix-ai
/plugin install cloudfix@cloudfix-ai
```

Then ask your agent, replacing the account and region:

> Use the co-gp2-to-gp3-guided skill to assess GP2 volumes in AWS account ACCOUNT_ID, region REGION, using my existing AWS access. Do not apply changes.

You need AWS credentials from your normal local setup. Never paste credentials into a conversation.

## Same skill, two places

The skill here is an exact copy of [`skills/co-gp2-to-gp3-guided/SKILL.md`](../../skills/co-gp2-to-gp3-guided/SKILL.md), which agents can also fetch directly without installing anything. A check on every pull request fails if the two copies differ. Edit the original, then copy it here.

## Licence

See [LICENSE](LICENSE).

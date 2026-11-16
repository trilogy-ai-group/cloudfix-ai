---
name: co-iac-pr
description: Turn a fixer's change.json into a reviewed draft pull request or a local patch in the repository that manages the resources, using only offline validators and never deploying anything. Use after a fixer skill has written change.json and the user wants the change made in infrastructure code.
---

# co-iac-pr: infrastructure-as-code PR worker

This skill is finder-agnostic. It knows nothing about any one resource type. Everything specific to a finder lives in the change note that `change.json` points at.

It has three parts: `## Orchestrate` runs in the main conversation, `## Worker` edits and validates, `## Reviewer` checks the result. Claude Code ships the last two as the `iac-pr-worker` and `iac-pr-reviewer` agents. Codex cannot ship agents, so everything the worker and reviewer need is in this file and in `review.md` next to it.

## Inputs

`change.json`, written by a fixer. Fields:

- `finder`, `account`, `region`: what produced the change and where the resources live.
- `resources[]`: for each resource, `id`, `current` settings, `target` settings, `assessed_target`, `deviates_from_assessment`, and `iac_evidence` (tags or other signals that suggest code manages it).
- `assessment`: the run directory of the assessment. Private; see the PR body rules.
- `change_note`: path to the finder's change note. It says what to edit for each tool, what never to change, which validators apply, and when an edit replaces a resource.
- `repo_hint`: a path or URL, or empty.
- `delivery`: `draft_pr` or `local_patch`.

Use `target` values exactly as written. Never substitute numbers from the assessment or your own calculation.

## Orchestrate

Run this in the main conversation. Do not ask a subagent to start another subagent.

1. Read `change.json` and the change note. Treat every "report replacement prominently or stop and ask" rule in the change note as binding on the worker and the reviewer.
2. **Find the repo.** Search, in this order, for each resource ID, its name and its `iac_evidence` values: `repo_hint`, the current checkout, then any other local folders the user names. Do not search GitHub or clone anything unless the user names the repository. If nothing is found, report that and stop. Never invent a repository, and never edit a repository that does not contain the resources.
3. **Read before editing.** The repository's agent instructions (CLAUDE.md, AGENTS.md), uncommitted local changes, existing open pull requests that touch the same files, and hooks and CI configuration. Some builds deploy on push or on merge; if a branch push or a PR would trigger a deploy, stop and tell the user before pushing.
4. **Dispatch the worker.** In Claude Code, start the `iac-pr-worker` agent. In Codex, start a subagent where available, otherwise run `## Worker` yourself as its own pass. Give it the full text of `change.json`, the change note path, the repository path, and the findings from step 2 and 3.
5. **Dispatch a fresh reviewer.** In Claude Code, start the `iac-pr-reviewer` agent; in Codex, a fresh subagent where available, otherwise a separate pass that does not reuse the worker's reasoning. Give it `review.md`, the worker's diff, the change note and `change.json`, not the worker's conclusions.
6. **Fix rounds.** If the reviewer returns findings of severity `blocker` or `major`, send them to the worker (the same agent through SendMessage if you can, otherwise a new one with the findings and the current diff), then review again. At most two fix rounds. If blockers remain after the second, deliver nothing as ready: report the open findings and stop.
7. **Deliver** according to `delivery`:
   - `draft_pr`: the worker pushes the branch and opens a draft PR. Return the PR URL, the base branch, the head branch and the diff stat.
   - `local_patch`, or `draft_pr` when pushing or opening a PR is blocked (no permission, no remote, a deploy trigger the user did not accept): produce the patch with `git format-patch` or `git diff` into a file outside the repository, and return its path and the reason.
8. **Report the effect** for each resource: the edit modifies the existing resource in place, replaces it, or only affects future launches. Take this from the change note's per-resource rules. Say "per provider documentation, not verified by a plan" unless the user asked for a plan and it ran. Put any replacement first, in plain words.

## Worker

Inputs: `change.json`, the change note, the repository path and what the orchestrator found about the repository. Do not start other agents.

1. Read the change note fully, and the repository's agent instructions. Follow them where they do not conflict with the safety rules below.
2. Confirm the repository manages each resource you are about to edit by finding its ID, name or `iac_evidence` value in code or configuration. A resource you cannot find stays out of the change; list it in your report as not found.
3. Create a branch from the repository's default branch (name it after the finder and the date, for example `cloudfix/<finder>-<yyyymmdd>`). Never commit to the default branch. Leave the user's uncommitted changes alone; if they overlap the files you need, stop and report.
4. Edit only what the change note describes, for only the resources in `change.json`. Use `target` values exactly. When `deviates_from_assessment` is true, still use `target`, and say in the PR body that it deviates, with `assessed_target` beside it. Respect the change note's "never change" list.
5. If an edit would replace a resource according to the change note, do not make it silently. Either stop and ask through the orchestrator, or make it only when the user already accepted replacement, and put the replacement at the top of the PR body and of your report.
6. Validate with offline validators only, chosen from the change note and the repository's tooling: `terraform fmt -check`; `terraform init -backend=false` then `terraform validate`; `cfn-lint`; `cdk synth` only where it runs without AWS credentials and without context lookups; the language's compiler or type check. Record each command and its result. If a validator is not installed, say so; do not install tools and do not substitute a command that needs credentials.
7. Never run `terraform plan`, `apply`, `destroy`, `cdk deploy`, `pulumi up` or `preview`, a change set, or anything that publishes, deploys, or needs AWS credentials or a remote backend. Do not set or read AWS credentials. The only exception is a plan the user explicitly asked for that the repository's own instructions allow.
8. Commit with a clear message naming the finder and the resources. Do not commit state files, lock files or generated output unless the repository commits them and the change requires it.
9. Deliver as the orchestrator directed. For a draft PR, push the branch and open the PR as a draft. Write the body by these rules:
   - Evidence summary only: for each resource, the current and target settings and why the change is justified, in a few lines. Never paste account-wide evidence, the whole assessment, other resources' details, account IDs beyond what the repository already shows, or credentials.
   - Refer to the assessment privately: mention that an assessment exists and its run identifier, never link or attach the run directory.
   - Say what the change does to existing resources, in the words of `## Orchestrate` step 8, and name every deviation from the assessment.
   - Write each paragraph as one continuous line and each list item as one line. GitHub renders a single newline as a hard line break, so never wrap prose at a column. Separate paragraphs with a blank line.
   - Do not restate CI check statuses (no "all checks green", no pass or fail lists). The Checks tab shows them live. Do include validator results that you ran locally, because CI did not make them.
10. Return: branch, head commit, diff stat, each validator command with its result, resources edited and resources not found, the effect per resource, anything you skipped and why, and the PR URL or the patch path.

## Reviewer

Follow `review.md` in this skill's folder. You are read-only: you may read files and run read-only git and shell commands, and you must not edit, commit, push or run any validator that writes. Return findings as a list, each with `file:line`, a severity (`blocker`, `major`, `minor`) and what to change. If there are none, say so and list what you checked.

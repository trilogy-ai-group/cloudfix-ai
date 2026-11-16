---
name: co-gp2-to-gp3
description: Assess AWS EBS gp2-to-gp3 conversion opportunities with a local read-only tool, or replay recorded evidence. Use when asked to find, check, size or explain GP2 volumes that could move to GP3, or to re-run a saved assessment. Assessment only; hands over to co-gp2-to-gp3-fix for any change.
---

# Assess GP2 volumes

This skill runs the `cloudfix` command-line tool, reads what it saved and explains it. The tool only reads AWS (STS `GetCallerIdentity`, EC2 `DescribeVolumes`/`DescribeInstances`, Pricing `GetProducts`). It needs no CloudWatch metrics or GitHub access and sends no data to its publisher. Replay makes no AWS calls. Never ask for credentials in chat or save them anywhere; the AWS profile name is the only credential-related value recorded.

## 1. Command form

Every `cloudfix` command in this skill runs as:

```sh
uv run --frozen --no-dev --project <plugin>/python cloudfix <args>
```

`<plugin>` is the plugin root: two directories up from this skill's own directory (Claude Code also substitutes `${CLAUDE_PLUGIN_ROOT}`). In the rest of this skill, `cloudfix ...` means that full command. The first command builds the tool's environment and takes a few seconds; later ones start immediately.

- If `uv --version` fails, stop and say uv is missing. Give the install options for the user's OS: the Astral installer (`curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS or Linux, `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows), Homebrew (`brew install uv`), WinGet (`winget install --id=astral-sh.uv -e`) or pipx (`pipx install uv`). Install only if the user asks you to.
- If uv reports that the plugin folder is not writable, rerun the same command with `UV_PROJECT_ENVIRONMENT` set to a per-user cache directory: `~/.cache/cloudfix-ai/env` on macOS and Linux, `%LOCALAPPDATA%\cloudfix-ai\env` on Windows. Use the shell's own syntax to set the variable for that command (`UV_PROJECT_ENVIRONMENT=~/.cache/cloudfix-ai/env uv run ...` in sh or bash, `$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\cloudfix-ai\env"` before the command in PowerShell, `set "UV_PROJECT_ENVIRONMENT=%LOCALAPPDATA%\cloudfix-ai\env"` in cmd). Keep using it for every later command.
- Any other uv failure (no network, blocked package index): report uv's own message and stop.

## 2. Start

```sh
cloudfix --version
```

Report any error line verbatim and stop. Do this before the interview so a broken setup surfaces early.

## 3. Interview

Run:

```sh
cloudfix gp2-to-gp3 describe
```

It prints the finder's settings as JSON: name, meaning, type, default and allowed values. The settings are `account`, `region`, `profile`, `volume_ids`, `cost_basis` (`public` or `supplied`), `costs_file`, `volume_age_threshold_days` (default 7) and `minimal_savings_usd` (default 0); `describe` is the authority if it differs.

Use the answers already in the request. Ask only for the settings it did not answer, one question at a time, and offer the default where there is one. Never apply a default silently: state the value you are using and that it is the default. If `cost_basis` is `supplied`, `costs_file` is required. Use only the requested account, region and volume IDs; do not widen to other accounts or regions. Use the AWS profile the customer names, or the default credential chain if they name none.

Write the answers as `config.json`, a JSON object keyed by the setting names, in a new run directory's parent (the run directory itself must not exist yet). Write every setting the user gave, and also every default the user accepted, explicitly with its value, so the state-and-confirm step is recorded in the artifact; the tool fills omitted settings with defaults silently, so omit only optional settings that have no value (for example `profile` when using the default credential chain, `volume_ids` for discovery, `costs_file` for public prices). Do not put credentials in it.

## 4. Run

For a live assessment:

```sh
cloudfix gp2-to-gp3 assess --config <config.json> --out <new-dir>
```

For recorded evidence, a simulation or a rerun with amended evidence:

```sh
cloudfix gp2-to-gp3 replay --evidence <evidence.json> --out <new-dir>
```

`<new-dir>` must be a new directory under an existing parent. Never write into an earlier run directory and never repair an earlier output. `assess` writes `config.json`, `evidence.json`, `context.json`, `result.json` and `summary.json`; `replay` writes all of them except `config.json`. The command prints one JSON line with `status` and the file paths.

Exit codes:

- **0**: the assessment completed, rejected candidates included.
- **2**: evidence is missing (replay only). Read `missing_evidence` in the summary, obtain that evidence, and rerun `replay` with the complete amended evidence into a new directory. The live tool fetches follow-up evidence itself.
- **1**: the run failed. The stdout line is `{"status":"error","error":{"code":...,"message":...}}`.

Read the saved artifacts on every outcome. On a failure, report the exact operation and parameters or the full error, and `failed_reads` from the summary; never turn an access failure into an empty result. An account mismatch stops the run; if the account was not known beforehand, confirm the returned identity before interpreting anything.

## 5. Read and explain the result

**Read the complete `summary.json`**, including every resource, failed check, pricing qualification and outstanding evidence request.

- `summary.json` is generated by code for interpretation. Use its decisions, exact settings, cost basis and execution status.
- `result.json` is the check ledger: the per-volume checks, sizing and estimates the decisions come from.
- `evidence.json` and `context.json` retain the inputs, read results, configuration and provenance. Read the complete relevant records when checking a finding or preparing a change; the summary's `artifacts` references must remain retrievable.

Pricing lookup counts and fallback status are in `cost_basis`. Full queries, units and raw prices are at `cost_basis.lookup_evidence`: resolve its `artifact` through `summary.artifacts`, open that file, and read the complete array at its `json_pointer`. Follow this whenever a fallback occurred (`fallback_used`, `fallback_count`), provenance is unknown, or pricing details matter to the decision. Do not treat an unavailable evidence file as verified pricing.

Report each resource's decision, failed checks, proposed GP3 settings when approved, estimated annual savings and pricing basis.

- Report dollar amounts from `estimate.display`; exact numeric amounts are for comparisons only. Do no arithmetic yourself.
- Use GiB for size, IOPS, and MiB/s for throughput. Preserve negative savings. An estimate with status `not_calculated` is unknown, not zero potential.
- Use the code-calculated `totals` for aggregate costs and savings. Report its status and the included, excluded and unknown resource counts, and use `totals.display` for money. Do not recompute totals or sum rounded per-resource amounts. Totals cover approved candidates with calculated savings; public-price and supplied-cost figures remain separate. A partial or unverified result must not be described as a complete account total.
- A volume whose `DontFixItTag` check failed is excluded by its owner's opt-out tag (`cloudfix:dontFixIt`), not by a technical limit. Report it as excluded by the owner's opt-out, not merely as a failed check or a rejection.
- Use each resource's `evidence_context` for recorded volume, attachment and instance states; absent state is unknown. A stopped instance does not prove that its volume is unused, and EBS storage remains billable while the instance is stopped.
- State the summary's `limitations`. Public prices and supplied costs are estimates unless independently verified. Identify any pricing fallback. Online modification depends on instance support; this assessment does not check instance-type compatibility or measure application performance. Do not promise equivalent-or-better performance, zero impact or immediate rollback.
- If `no_candidates` is true, say no GP2 volumes were found in scope; that is a result, not an error.

Keep assessment, proposed change, execution and observed savings separate. `execution_outcome` is `not_executed` and `observed_saving` is empty: an assessment is not a change and an estimate is not a realized saving. Return the paths of the run directory's artifacts.

## 6. Next step

An assessment-only request ends here. For any change (a PR, a patch, or a direct AWS modification), hand over to the `co-gp2-to-gp3-fix` skill with the run directory. Do not prepare or perform a change from this skill, and do not treat an approved assessment as authorization to execute one.

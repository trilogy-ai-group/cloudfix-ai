# Assess gp2 to gp3 volume conversion: the guided path

This is the procedure `SKILL.md` sends an agent to when it may not run code or lacks `python3` or the AWS CLI; `scripts/assess.py` implements the same rules. Where a reference says `SKILL.md`, it means this file. `skill.id` is `co-gp2-to-gp3`, `skill.revision` is the version in `SKILL.md` and `run.mode` is `guided`. With no AWS read tool either, say so; the user can run `scripts/assess.py` themselves (`--help` lists the arguments) and share the output folder. Never ask for credentials.

Use the customer's existing read tools and calculator. Keep credentials and complete evidence with the customer. Assessment does not authorize AWS changes.

- This guided path is this file plus the other files in this folder.
- It needs no executable, CloudFix service, dependency installation or particular language runtime. Do not invoke `scripts/assess.py` to perform this variant. Do not call a hosted CloudFix specialist or require access to the CloudFix source repository.
- All rules needed to assess the supplied resource are included in this package.
- The algorithm is transcribed from the same pinned finder and local adapter as the code-based variant, including its defaults and conditional behavior. The evaluator is the customer harness, not the native TypeScript binary.
- Use a calculator or an existing local execution tool for every formula, comparison and aggregate; retain inputs, expressions and unrounded outputs. Do not do cost arithmetic in model prose or replace the specified rules with remembered AWS advice. If the needed calculation cannot be executed, report that limitation instead of inventing numbers.
- Describe the result as a **skill assessment**, never as native finder JSON or an independently verified saving.

Read this whole file before collecting data. Its stages define rules; they do not authorize collecting billing first and validating its prerequisites later. Each stage names the reference that holds its exact rules: read that file in full when the stage says so, before acting, and follow it exactly. Do not replace a formula or add a finder validation from general AWS knowledge. Scope, evidence and reporting instructions govern the harness workflow; they are not extra finder validations.

When to read each reference:

- `references/scope-and-access.md`: stage 1, always.
- `references/pricing.md`: stage 1, before the Pricing lookups; used again in stage 4.
- `references/collection.md`: stage 2, before the first EC2 read.
- `references/billing.md`: only in `billing` mode, in stage 2 before any Cost Explorer call.
- `references/checks.md`: stage 3, before evaluating any volume.
- `references/savings.md`: stage 4, before calculating any saving.
- `references/assessment-guide.md`: stage 1, always. It fixes the shape of the result.
- `references/assessment-v1.schema.json`: stage 5, when checking `assessment.json`.
- `references/change-proposal.md`: only when the user asks how to apply the change, or asks for a patch, pull request or AWS procedure.
- `references/native-output.md`: only when the user explicitly asks for a native-shaped projection, a replay packet for the bundled finder, or native-reader metadata.

Keep the run fast without changing its order. Issue independent reads in one step: the four Pricing lookups together, and the volume or instance batches together. Never reorder the access sequence in stage 1 to save time. Beyond a handful of volumes, produce the calculations, `assessment.json` and the report table with a short script over the saved evidence instead of typing them by hand; the script applies the formulas in the references unchanged. Parallel workers are optional and help only across independent regions or accounts.

## 1. Scope and access

Read `references/scope-and-access.md`, `references/pricing.md` and `references/assessment-guide.md` now. Read every other reference only at the stage that names it.

Establish account, regions, resource selection and pricing mode. State required reads, defaults and known API charges. Verify identity and relevant access. Report missing permissions without broadening them.

- **Pricing mode.** `public_prices` (public list prices), `supplied` (costs supplied by the user) or `billing` (Cost Explorer estimate). If unspecified, resolve that choice once. The finder's two savings checks are part of every decision, so there is no `technical_only` mode.
- **Access order, which must not be reordered.**
  1. Verify identity and the expected workload account;
  2. perform the four Pricing lookups and select and validate their dimensions, units, rates and fallbacks;
  3. make the scoped dry-run check immediately before each needed EC2 read where the tool exposes it;
  4. read the volume inventory and the needed instances;
  5. after a nonempty inventory, check every size and calculate every public baseline;
  6. and, in `billing` mode only, save the pre-billing snapshot before either Cost Explorer call.

## 2. Collect evidence

Read `references/collection.md` now.

Use its exact request shapes for the selected volumes and for the instances in the first attachment of every parsed volume, in batches of up to 200, collecting every page. Issue independent reads together. Save complete responses, timestamps and errors. A completed empty inventory differs from missing or denied data. If the inventory read is denied, stop there: make no read that only matters once resources are known. Request precisely what is missing. Retain the original snapshot when retrying a dependent read.

Save the evidence as JSON files in an `evidence/` folder beside `assessment.json`, and never edit a saved response:

- One file per operation: `identity.json`, `pricing.json`, `describe-volumes.json`, `describe-instances.json` and, in `billing` mode, `pre-billing-snapshot.json` and `billing.json`. Supplied input is saved unchanged as `supplied-costs.json`. `run-metadata.json` holds `{run_metadata: {accountId, region, asOf, configuration, configuration_overrides, run_classification, experiment}, request: {pricing_mode, resource_ids, allow_public_fallback, profile, billing_profile}}`; a replay needs it.
- Each file is an array of read records in the order made, one per page, batch, retry or failure: `{service, operation, region, profile, input, observed_at, output}` with the complete original input and output and the real observation time. A failed read keeps `{error: {code, message, aws_request: {action, profile, region, input}}}` and its diagnostics instead of an output. The expected `DryRunOperation` response is an authorized access check, not a failed read.
- `calculations.json` holds, per volume ID, the inputs, expressions and unrounded outputs of every formula, with the resolved prices and their selected dimensions and fallbacks.

In `billing` mode only: after the pre-billing snapshot is saved, read `references/billing.md` and make the two Cost Explorer calls exactly as it specifies. Complete recorded Cost Explorer responses are processed without new calls. When recorded evidence is replayed, the snapshot is recomputed from the saved identity, pricing and volume reads and written to the output folder; a supplied evidence folder without `pre-billing-snapshot.json` is not a gap.

## 3. Analyze

Read `references/checks.md` now.

Apply the finder's checks to each volume in this order, using the reference's exact rules, value shapes and control flow: `ResourceExists`, `ResourceFixed`, `VolumeExpectedType`, `VolumeAgeXDays`, `EMRClusterAssociation` (or `Ec2InstanceDescribable` in its place), `ElasticVolumeEnabled`, `MinimalSavings`, `MaximumSavings`.

Record each check as it was actually evaluated. Never infer that an unobserved check passed. Calculate proposed settings with the reference's sizing formula only for volumes that pass the applicable resource checks; keep settings for any other volume as provisional details, never as a recommendation.

Decide each volume by the ordered rules under Decisions in the guide.

## 4. Calculate costs

Read `references/savings.md` now. It builds on `references/pricing.md` from stage 1 and, in `billing` mode, on the rate from `references/billing.md`.

Calculate the public gp2 and gp3 annual prices from the resolved prices, take the current annual cost from the requested pricing mode, and apply the finder's savings formula and report fields exactly as written. Then complete the savings-dependent checks `MinimalSavings` and `MaximumSavings` on the unrounded values. Use a calculator or script for arithmetic and keep unrounded results. Label the actual price basis and every fallback or input substitution. Unknown amounts stay unknown.

Never substitute list prices for a failed billing request unless the customer allowed that fallback; then follow the guide's conditional-request rule for the whole run. A non-finite result or missing settings for an otherwise approved volume is an assessment error, not an estimate.

## 5. Verify

Write the draft `assessment.json` before this stage. The self-check reads that draft and the saved evidence; stage 6 saves the checked file and writes the customer report from it.

Do one self-check on the saved files, not from memory, using the same calculator or script tools. It tests compliance with these rules; it is not a new optimization algorithm or an extra finder validation.

- **Coverage.** Every selected or discovered volume has exactly one row or is listed as unassessed; counts match the inventory pages; for live collection, identity, scope, the four resolved prices and their provenance, each ID, size and public baseline agree with the raw responses; the required read order was followed and, in `billing` mode, the pre-billing snapshot was saved before Cost Explorer.
- **Arithmetic.** Recompute every sizing and cost expression, each check's comparison and the check order and control flow from the saved evidence. Require finite `annualSavings` and every calculated numeric report field, plus complete settings, for a recommended volume.
- **Totals.** Recompute the decision counts and the unrounded totals from the rows.
- **Evidence.** Open every evidence reference and confirm it resolves to the complete saved record, including all pages, failures, qualifications and, in `billing` mode, the billing source, period and full billing evidence.
- **Schema.** Validate `assessment.json` against `references/assessment-v1.schema.json` if a validator is already available; otherwise check the required fields by hand and say so. Never install one. A short script that checks the draft's required fields, fixed values and date-times against the guide counts as that check; say that it was not a full schema validation.

Record the result in `verification` with `method` `self_check` and a `summary` of what was and was not checked.

- Problems found become `issues`; an unresolved material problem or missing external prerequisite leaves the run `partial` or `failed` with a complete draft report.
- A valid rejected candidate or faithfully reported access failure can verify clean; a clean verification does not turn it into an approved recommendation.
- Correct only supported defects using the skill and existing evidence; retain the original evidence, failed outputs and earlier verification records; never hand-edit an answer to agree with an expected or native output or erase the failed attempt.
- A sequencing failure cannot be repaired by changing timestamps: retain the failed run and collect a new correctly ordered snapshot only within existing authorization.

Independent review is optional. Use `independent_review` only when a separate reviewer actually examined the complete evidence; never call a self-check independent, and never create a review record, checkpoint file or hash for a review that did not happen. Report verification separately from resource decisions and execution status.

## 6. Report and hand off

Complete stage 5 on the saved files before returning the assessment or a proposed change. `references/assessment-guide.md`, read in stage 1, fixes this skill's literal values, the field meanings, a filled example, the totals rule and the report layout.

Produce three things, in this order:

1. The saved raw evidence files from stages 2 to 4.
2. `assessment.json`, written as the guide describes and checked against `references/assessment-v1.schema.json` in stage 5. Retain every result, missing input and material qualification, with references to the complete evidence.
3. The customer report in the guide's layout: estimated savings first, then the tables and qualifications, using only numbers held in `assessment.json`. This local skill does not require CloudFix registration.

Native-shaped projections, replay packets and native-reader metadata are optional: produce them only on explicit request, following `references/native-output.md`, and point to them through the optional `source_output` field.

Then offer the next step: show each recommended volume's change (gp3 with the returned IOPS and throughput) with its estimate, never as a realized saving, and ask whether to proceed; proceeding means the proposal in `references/change-proposal.md` (a pull request or a handoff to whoever holds write access), prepared separately and carrying the unresolved prerequisites. Give every other volume its next read. On a long run, give short progress updates and state the elapsed time at the end. This skill does not execute it: assessment, a procedure or a pull request never authorizes an AWS write, and `execution_status` stays `not_executed`. Any later change is the customer's own action under their own authorization.

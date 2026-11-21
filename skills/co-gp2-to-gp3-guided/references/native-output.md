# Optional native-shaped outputs

Read this only when the user explicitly asks for a native-shaped projection, a replay packet for the bundled finder, or native-reader metadata. None of it is needed for `assessment.json` or the customer report, and none of it changes a check, formula or decision. When produced, save these files under `evidence/` and point each finding's optional `source_output` at its projection row: `{"kind": "guided_projection", "evidence_ref": "evidence/native-projection.json#/resources/0"}`. A projection written by an agent is never native finder JSON. Stage numbers refer to `SKILL.md`.

## Native-shaped projection

The native-shaped projection uses source field names for comparable facts (`resourceId`, `annualCost`, `publicAnnualCost`, `annualSavings`, `gp3Iops`, `gp3Throughput`, and validation `name`, `approved`, `type`, `value`), plus `assessment_kind: "guided_transcription"`, the final `cost_basis`, source references, scope/time/configuration, calculation traces, totals and unresolved evidence. `RecommendationFinalized` remains explicitly labelled as the same empty-history adapter assumption.

For each resource retain `savings_calculated`. A pre-calculation rejection may retain source-initialized `annualSavings=0` for comparison, but its customer-facing saving must be null/unknown, not “saves $0”. A real savings-gate rejection retains calculated amounts, including negative values.

For each resource the projection holds:

- Native `decision`: `proceed`, `do_not_proceed`, `needs_evidence`, or `assessment_error`; this is a **skill result**, not a serialized native finder verdict. A missing input/read is `needs_evidence`; a completed failed source check is `do_not_proceed`; a denied/failed read is an error. Use `proceed` only for the applicable resource/savings checks and state the unavailable CloudFix-only checks separately. The common `decision` follows from it through the guide's mapping.
- `approved` for all algorithm-ledger checks, `stopProcessing` for a fail-fast rejection, and each check's native `name`, `approved`, `type`, parsed `value`, evidence and reason; list checks skipped after fail-fast separately. A supplied successful empty instance read produces the source's `Ec2InstanceDescribable=false` rejection; it is not an absent-response request.
- When approved, unchanged size and target `gp3`, `gp3Iops`, `gp3Throughput`, plus the six sizing intermediate values.
- Current annual cost, target annual cost, annual saving and `savingsPercentage`; the separate `publicSavings` amount and `publicSavingsPercentage`; public-cost breakdown, discount-ratio display value, price source/date/fallbacks and all input substitutions. Preserve these explicit calculated fields when a savings gate rejects the result too. When rejected, retain the reasons; do not present hypothetical settings as an approved change.
- `execution_outcome: not_executed` and `observed_saving: null`. An estimate is not an observed saving.

## Run metadata in every saved file

The shared run-metadata object is defined in `scope-and-access.md`.

Copy this object into every saved assessment, auxiliary selected-scope/input snapshot, request/calculation record, verification record and retry output, using `run_metadata` where necessary. In the replay packet those fields also belong in `run_context`, never as extra fields inside strict `evidence`. Preserve raw AWS response bodies unchanged inside their recorded `output`; put metadata beside them. Save strict evidence only inside the packet, or pair any separate `evidence.json` with the complete `context.json` and include both at every handoff. Read back every saved file to verify metadata and evidence survived; do not assume that a complete main report compensates for an incomplete input copy. Human-readable reports must identify the classification and any experiment/overrides and link the complete context.

## Exact replay and pricing-provenance contract

Save `replay-packet.json` with top-level `evidence` and `run_context`. `evidence` uses the strict input contract in `scope-and-access.md`, with every required AWS response and the accepted resource costs. `run_context` uses the **exact field names below**. Do not substitute `pricingLookups`, `billing`, `selectedResourceIds`, or `cost_basis: "billing_estimate"`: the native reader does not interpret those aliases. This is an output contract; using it does not require downloading or running the native executable.

Structural template for a completed CE assessment (unquoted identifiers stand for actual recorded/computed values; serialize valid JSON, never literal placeholders):

```js
{
  evidence: completeEvidence,
  run_context: {
    ...sharedRunMetadata,
    mode: "live_collection", // "replay" for recorded input
    status: "complete",
    scope: { account: accountId, region, selected_volume_ids: selectedIds,
             discovery: discoveryMethod }, // "DescribeVolumes gp2 inventory" or "selected_ids"
    reads: completeResourceAndPricingReadRecords,
    pricing_lookups: completePriceLookupRecords,
    cost_basis: "ce_account_region_gp2_effective_rate",
    billing_request: { status: "complete", profile: billingProfileOrNull },
    cost_input: {
      currency: "USD",
      source: "AWS Cost Explorer account/region GP2 effective-rate estimate; not per-volume billed cost",
      period: periodDescription,
      resources: acceptedResourceCosts,
      billing_evidence: completeBillingRecord
    },
    resource_collection: frozenPublicCollection,
    qc: { checkpoint_b: { artifact: declaredFinalReviewPath } }
  }
}
```

`sharedRunMetadata` is the object defined in `scope-and-access.md`. `acceptedResourceCosts` must equal `evidence.resources`, including exact numeric amounts. `periodDescription` is `"<start> <= usage < <end> UTC; recorded current volume capacity annualized at 12 months"` using actual UTC dates. `frozenPublicCollection` contains `{status:"complete", evidence, context}` captured before CE, with public-only costs explicitly labelled `public_price_estimate`; the final evidence receives CE costs without altering that original snapshot. Keep partial collection as `status:"incomplete"` on failure. `qc.checkpoint_b.artifact` is optional: it is the path of a saved independent review of this packet. Include `qc` only when such a review actually ran and its record is saved at that path; otherwise omit `qc`. Never create the field, a review file or a hash to make a review appear, and do not rewrite a reviewed packet to embed its review. Do not fabricate a native build identifier or monitoring records.

Each `pricing_lookups` entry has `{query, region, unit, raw_price, fallback_used}`. Copy the complete actual GetProducts request into `query`, the priced workload region into `region`, and use the exact unit `GB-Mo`, `IOPS-Mo` or `GiBps-mo`. `raw_price` is the selected USD dimension **before** unit conversion (throughput is the GiBps-month rate before division by 1024), or null for an actual successful lookup with no price. `fallback_used` is true for null/undefined price and also for zero throughput, following `pricing.md`; an access error is not a fallback. Retain selected SKU/dimension, publication/effective dates and matching full raw read alongside these required fields. The four entries must be supported by the corresponding complete responses; never add entries merely to make a native “pricing verified” label appear.

Every resource/Pricing/billing read record contains `{service, operation, region, profile, input, observed_at, output}` with the complete original input/output and real observation time. Failure preserves `{error:{code,message,aws_request:{action,profile,region,input}}}` and original diagnostics instead of a fabricated successful output. The expected EC2 dry-run `DryRunOperation` is an authorized access check, not a failed read: record `access_check:{status:"authorized",response:{code:"DryRunOperation",message}}` with the actual response/diagnostics, without setting `error` or fabricating resource output. Keep all pages and failed attempts. Store resource/Pricing reads in `run_context.reads`; the complete billing reads also belong in `cost_input.billing_evidence.reads` on success or `billing_attempt.reads` on failure. Preserve the raw AWS body unchanged, with run metadata outside it.

`completeBillingRecord` must contain these fields with their real evidence, not empty stubs:

- `method: "ce_account_region_gp2_effective_rate"`, `status: "complete"`, `estimate: true`, `currency: "USD"`, `started_at`, `completed_at`, and the actual `billing_identity`.
- `scope: {account, region, selected_volume_ids}` matching the workload exactly, plus `baselines` entries `{resourceId, sizeGiB, publicAnnualCost, public_baseline_source:"public_price_assessment_resource"}` for the same selected IDs. Each recorded GP2 size is finite, positive and equal to its saved response. No duplicate, missing or extra IDs across scope, baselines, costs or evidence. Neither the account nor region becomes the payer's scope.
- `period: {start,end,days:31,timezone:"UTC",end_exclusive:true}`, actual `usage_types` entries `{value,unit}`, `cost_filter`, full `reads`, and all `periods` rows retaining `TimePeriod`, `Total`, `Groups`, `Estimated`, `selected_cost` and `net_metric_fallback`.
- `totals: {net_cost, unblended_cost, usage_quantity}` (`net_cost` is the sum of the selected net-or-permitted-fallback costs), `effective_monthly_gp2_rate`, verified `quantity_unit`, `net_metric_fallback_periods` as a count, `estimated_data` as a boolean, `coverage: {periods_returned,requested_days,missing_days}`, and `allocations` equal to the accepted resource costs. Preserve each estimated/fallback/missing period, not just these counts.
- The complete `qualifications` from `billing.md` and all actual `issues`, configuration, supplied metadata, earlier failures and provenance. Keep extra original fields; the native reader's minimum fields are not permission to discard evidence.

For an explicitly requested **public-price** assessment, use `cost_basis:"public_price_estimate"`, `cost_input:null`, no fabricated billing record, and the real pricing lookups. Source/period in the native cost summary may legitimately be null for that mode. For **supplied costs**, use `cost_basis:"caller_supplied_costs"` with the supplied `{currency,source,period,resources}` unchanged; do not invent CE provenance. Raw fixtures lacking provenance remain `supplied_evidence` with unknown price verification.

For **billing failure**, use `status:"error"`, `cost_basis:"billing_estimate_unavailable"`, `billing_request:{status:"failed",profile}`, the complete `error` and `billing_attempt` (`status:"error"`, all prior reads/qualifications), and the retained `resource_collection`. Accepted `evidence.resources[].annualCost` is null; keep each `resourceId` and `publicAnnualCost` unchanged. The original public `annualCost` remains in the explicitly labelled collection snapshot. Do not run the finder or produce savings from those unaccepted costs. A request still collecting resources/billing remains pending/unavailable. For **completed empty inventory**, use `no_candidates:true`, empty resources/selected IDs and all completed empty GP2 inventory pages; dry-run, denial or absent inventory is insufficient. A billing-mode empty run uses `billing_request.status:"not_needed"` and `cost_basis:"not_applicable"`; a public-mode empty run retains `cost_basis:"public_price_estimate"` without a billing request.

At every handoff preserve the complete packet/context, including caller metadata and verification findings. If supplied historical records contain earlier reviews or waivers, retain them as history; they are not prerequisites for new CE calls and must not be replaced with a fabricated pass. Native replay promotes known fields such as `scope`, `reads`, `pricing_lookups`, `cost_input`, `billing_attempt`, `billing_request`, `resource_collection`, `configuration`, `experiment` and `cost_basis`; other original fields remain under `context.original_input.run_context`. Repeated replay must retain that original-input chain too. Native mode/status/classification are derived rather than trusted labels. A supplied status alone is not a general error-replay guard; retain the actual failed read and incomplete evidence. A prior billing failure specifically stays failed under the contract above. The native summary references `/pricing_lookups` and `/cost_input/billing_evidence` in its context artifact; retain these paths even when also providing a shorter human summary.

## Checking these outputs

When these outputs were requested, the stage 5 self-check also covers them:

- All source checks and their order/control flow, every sizing/cost expression and unrounded total, counts and complete resource coverage, raw evidence and pagination, pricing/billing qualifications, the exact metadata contract above, preserved failures/experiment metadata and consistency between prose and machine fields.
- For a successful live CE assessment: the actual `cost_input.source`, `cost_input.period`, all four `pricing_lookups` and the full `cost_input.billing_evidence` against the raw records. Resolve the complete evidence behind every reference in the output. Missing or inconsistent fields require correction or an explicitly unavailable result, never a success with blank billing labels.

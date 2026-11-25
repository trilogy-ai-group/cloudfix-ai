# Optional native-shaped projection, legacy fields and replay packet

Read this only when the customer explicitly asks for the native-shaped projection, the legacy per-resource fields or a replay packet. None of it is needed for `assessment.json` or the customer report, and it never changes a decision. What this file produces is a reconstruction from the saved evidence: label it `guided_projection`, never `native`.

Save the projection as `native-projection.json` beside `assessment.json`, keyed by distribution ID, and point each finding at its entry with the optional field `source_output:{"kind":"guided_projection","evidence_ref":"native-projection.json#/<distributionId>"}`.

## Report fields set by rule 3

Immediately after evaluating rule 3, set these exact fields even on an ordinary rejection:

```text
finderReportData = {
  resourceId,
  cachePolicyConfigured: cachingDisabledPolicyCount === 0,
  compressionEnabled: compressionDisabledBehaviorCount > 0
}
resourceFixedConfiguration = {
  compressEnabled: compressionDisabledBehaviorCount > 0
}
```

The source's names `compressionEnabled` and `compressEnabled` are misleading: **true means at least one behavior has compression disabled**, not that compression is enabled everywhere. Preserve the original fields while explaining their meaning accurately.

## Cleanup

For final native rejection, cleanup removes fixerParameters but retains incidental finderReportData/resourceFixedConfiguration/tags and zero annualSavings. An approved item retains `{distributionId}` and gets the finder report. Native report data contains the three fields above; it does not include a new-cost field or dataTransferred. Return all original resource fields separately.

## Per-resource fields

Per resource return:

- `assessment_decision`: `eligible`, `rejected` or `needs_evidence`; `technical_eligibility` separately, plus `financial_assessment_complete`, missing inputs and every unevaluated check. A failed native Exception row may coexist with harness `needs_evidence`.
- `native_equivalent_projection`: unchanged original resource; ordered evaluated rows in **`validations:[{name,type,approved,value}]`** (never a renamed `validationResults` field) where `value` is JSON text, dates ISO and undefined keys omitted; approved/stopProcessing where determinable; final `annualSavings:0`, `otherImpactedNodeIds`, all `finderReportData`, `resourceFixedConfiguration`, `resourceTags` and retained `fixerParameters`. Preserve these exact field names; do not alias `resourceTags` to `tags`. Unknowns stay outside native-shaped rows. For missing history, set `framework_context_complete:false`. A batch exception or absent material evidence prevents claiming a complete native outcome.
- `financial_summary`: the per-resource record of `references/historical-cost-illustration.md` ("Rule 4 and the financial summary"). In `technical_only` mode this is `not_requested`, not a blocker or an invented financial pass.
- Complete evidence references, skill version, any supplied source revision, assumptions, unknown platform context, errors, warnings and unchanged caller metadata. The totals rules are in `references/historical-cost-illustration.md`. Return `execution_status:"not_executed"`.

In `technical_only` mode: return `financial_assessment_complete:false`, `financial_assessment_status:"not_requested"`, and unknown current cost/physical savings. Complete technical evidence may yield `assessment_decision:"eligible"` while `native_projection_complete:false`.

## Replay packet

Save a private replay packet containing the complete original inputs/run_metadata, scope and discovery coverage, all raw reads/pages/errors/attempts, normalized projections, cost provenance and billing window, calculations with unrounded results, every resource decision, qualifications and the verification record. Preserve unknown caller fields through normalization, saved-input copies, retry, handoff and replay. Verify the saved packet can be read completely. Replays use the same observation snapshot and configuration; changed observations are a new run.

## Value serialization and byte limits

Native validation JSON spells nonfinite numeric values as strings (`"NaN"`, `"Infinity"`, `"-Infinity"`) and invalid dates as `"Invalid Date"`; undefined object keys are omitted. Preserve those distinctions. If a native validation value exceeds 65,536 UTF-8 bytes, its serializer produces `{truncated:true,originalBytes,head:<first 8192 UTF-8 bytes>}`. A projection of that wrapper must also link a verified complete value artifact; neither the raw evidence nor the review findings may be discarded to match a native size limit.

For each native-shaped validation row, serialize its `value` as JSON text with the source rules: valid Dates become ISO strings, invalid Dates become `"Invalid Date"`, and nonfinite numbers become their strings `"NaN"`, `"Infinity"`, `"-Infinity"`. Undefined object keys omit. Reject Map, Set or Error objects instead of silently serializing them to `{}`; preserve the original unsupported value/type as a serialization error. Standard JSON failures (for example cycles) remain errors. Count **UTF-8 bytes**, not characters. Above 8,192 bytes record an oversized warning; above 65,536 bytes the native row value becomes the JSON text of `{truncated:true,originalBytes:<full JSON byte count>,head:<longest complete-code-point prefix of the full JSON within 8192 bytes>}`. Exactly 65,536 bytes is not truncated. An Exception's observed stack is separately limited to the longest complete-code-point prefix within 4,096 bytes before native row serialization.

## Complete evidence survives the limits

These native limits apply only to the projection, **never to retained evidence or verification findings**. Save the full raw response/error/stack and full pre-limit normalized validation value in a private complete artifact, link that artifact beside the affected native row, and verify readback at normalization, persistence, handoff, retry and replay. Every consumer, and any optional reviewer, must retrieve it, including findings beyond a displayed summary. Preserve caller metadata, earlier attempts and every verification finding unchanged. If a required full record cannot be stored or retrieved, fail explicitly; a truncated native value plus a short summary is not sufficient evidence.

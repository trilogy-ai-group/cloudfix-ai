# Optional: native-shaped projection, exception rows and replay packet

Read this only when the customer explicitly asks for output shaped like the CloudFix finder's own JSON, or for a replay packet. A normal assessment does not need it: `assessment.json`, the evidence files and the customer report are complete without it. When produced, save the projection as an evidence file and point to it from the finding's optional `source_output:{kind:"guided_projection",evidence_ref}`. A reconstruction is never labelled `native`.

## Projection fields

The saved `native_equivalent_projection` holds: original resource; ordered evaluated rows in **`validations:[{name,type,approved,value}]`** (never a renamed `validationResults` field) with **value as JSON text**; determinable approved/stopProcessing; annualSavings; otherImpactedNodeIds; every populated finderReportData field; known `fixerParameters`, `resourceTags` and `resourceFixedConfiguration`. Preserve these exact field names; the tag record belongs in `resourceTags`, never a renamed `tags` field. Dates serialize as ISO; undefined keys omit; nonfinite numbers and invalid dates retain their native string spellings. Preserve full values/evidence even where native serializers truncate. Unknown framework/check state lives explicitly outside native-shaped rows, with `framework_context_complete:false` when unavailable; standalone eligibility does not assert an unobserved RecommendationFinalized pass.

## Value serialization and byte limits

For each native-shaped validation row, serialize its `value` as JSON text with the source rules: valid Dates become ISO strings, invalid Dates become `"Invalid Date"`, and nonfinite numbers become their strings `"NaN"`, `"Infinity"`, `"-Infinity"`. Undefined object keys omit. Reject Map, Set or Error objects instead of silently serializing them to `{}`; preserve the original unsupported value/type as a serialization error. Standard JSON failures (for example cycles) remain errors. Count **UTF-8 bytes**, not characters. Above 8,192 bytes record an oversized warning; above 65,536 bytes the native row value becomes the JSON text of `{truncated:true,originalBytes:<full JSON byte count>,head:<longest complete-code-point prefix of the full JSON within 8192 bytes>}`. Exactly 65,536 bytes is not truncated. An Exception's observed stack is separately limited to the longest complete-code-point prefix within 4,096 bytes before native row serialization.

In the next paragraph "QC findings" means the findings of the self-check or of an optional review.

These native limits apply only to the projection, **never to retained evidence or QC findings**. Save the full raw response/error/stack and full pre-limit normalized validation value in a private complete artifact, link that artifact beside the affected native row, and verify readback at normalization, persistence, handoff, retry and replay. Every consumer, and any optional reviewer, must retrieve it, including findings beyond a displayed summary. Preserve caller metadata, earlier attempts and every QC finding unchanged. If a required full record cannot be stored or retrieved, fail explicitly; a truncated native value plus a short summary is not sufficient evidence.

## Exception rows

**Exact per-item exception contract.** A fault caught inside a wrapped validation callback uses `context:"Validation error: "`. A fault caught inside parameter population uses `context:"Error while populating parameters: "`. Both literal strings end with one space; retain it. The row is `{name:"Exception",type:"Other",approved:false,value:<serialized {context,error,errorName,stack}>}` and sets the item `approved:false,stopProcessing:true`. For an actual Error, `error` is its message and `errorName` its name; for a thrown non-Error, `error=String(thrownValue)`, `errorName:null`, `stack:null`. Preserve the observed stack when available under the native serialization limits, otherwise null; never invent a stack or infer an unrecorded error class. Preserve full diagnostics separately. Only an evidenced thrown fault at the stated boundary produces this row: a missing unattempted read or an ordinary failed validation is not an Exception. Later source stages retain their existing skip, savings and cleanup rules.

Asset searches run inside parameter population, so a thrown SearchAnalyses/SearchDashboards/SearchDataSets/SearchDataSources error uses the parameter-population context. Exceptions within the wrapped validation rules use the validation context. Swallowed ListUsers/tag errors and top-level probe/activity errors retain their separate outcomes; do not convert them into these per-item rows.

## Replay packet

A replay packet is the evidence saved in stage 2 of `SKILL.md`, complete and unchanged, together with `assessment.json` and this projection, so the run can be reproduced without live calls. It adds no new reads. Keep it with the customer.

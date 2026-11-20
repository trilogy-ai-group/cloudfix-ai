# Optional: native-shaped projection and replay packet

Read this only when the user explicitly asks for output in the finder's native shape, for a replay packet, or for a field-by-field comparison with CloudFix output. Nothing here is needed for `assessment.json` or the customer report, and nothing here changes a check, a decision or an amount.

When it is requested, save the projection as `evidence/native-projection.json`, one entry per resource, and point each finding at its entry with the optional field `source_output`, for example `{"kind":"guided_projection","evidence_ref":"evidence/native-projection.json#/0"}`. A reconstruction made by following this skill is always `guided_projection`, never `native`.

## Projection contents

Per resource:

- `native_equivalent_projection`: original resource, ordered native-shaped rows `{name,type,approved,value}` for evaluated rules (`value` is JSON text; dates ISO, undefined keys omitted), approved/stopProcessing where determinable, annualSavings, otherImpactedNodeIds, all finderReportData, optional known fixerParameters and `resourceTags`. The ordered row array is `validations`, never an alias such as `validationResults`. Represent unknowns explicitly outside native-shaped rows; never label a skipped/unavailable check as passed. Include `framework_context_complete:false` when finalization/configuration/operational context is unknown. Standalone eligibility does not assert the unknown framework row passed.

## Report data

When cost calculation is supported, merge these exact fields into `native_equivalent_projection.finderReportData` for every item, retaining the earlier volume/metrics fields: `{resourceId,annualSavings,newCost,annualCost,immediateArchival,snapshotPricePerGBMonth:archiveSnapshotPrice}`. Here the report's `immediateArchival` comes from the fixer parameters assigned after technical finalization; omit it if those parameters were never assigned. If only a later savings gate rejects the resource, retain this previously populated report field even though final cleanup removes fixer parameters. Record the computed archive cost separately as well. Do not leave these native report fields only in an auxiliary human-readable/financial summary. Missing financial inputs leave them explicitly unknown outside the native-shaped fields, never initialized zero presented as a cost result.

## Fixer parameters and tags

If native operational context is supplied, fixer parameters for approved resources are `resourceId`, `immediateArchival`, `snsTopicArn` from CloudFix monitoring configuration, and `alarmParameters:{tenantId,finderFixerId,resourceId,awsRegion,awsAccount,recommendationId}`. Preserve supplied values; unavailable CloudFix operational identifiers stay unknown, never dummy ARNs/IDs. These parameters describe the original result; this skill does not execute the fixer. `resourceTags` copies all present Key/Value pairs (last duplicate wins); optional CloudFix account-owner/l4/l5 virtual tags require real supplied context.

## Serialization

Native validation `value` is JSON **text**, with special numeric/date handling: nonfinite numbers become `"NaN"`, `"Infinity"` or `"-Infinity"`; an invalid Date becomes `"Invalid Date"`; valid dates become ISO text and undefined object keys are omitted. Map, Set or Error objects in a value cause an error, not `{}`. Native serialization warns above 8,192 UTF-8 bytes and, only above 65,536 bytes, emits JSON text for `{truncated:true,originalBytes:<full JSON UTF-8 byte count>,head:<first 8,192 UTF-8 bytes without splitting a code point>}`. Native exception `stack` keeps at most 4,096 UTF-8 bytes on a code-point boundary. These bounds describe the **native-shaped projection only**: retain the complete original value, diagnostics and stack in private evidence and references, and verify that reviewers/replay consumers read them. Never apply these bounds to evidence, findings, caller metadata or the assessment ledger. If complete records cannot be preserved and delivered, return an explicit error rather than a partial success.

## Replay packet

Save a private replay packet containing `run_metadata`, complete original inputs, scope/discovery method, all read records/pages/errors, normalized data, cost input/provenance, the requested economicsMode, native pricing resolution/fallback, complete regional quotes and separately labelled public_price_model/regional_estimate/model_checks when selected, expressions/results, per-resource decisions, qualifications and the verification record. Preserve extra caller fields; this is not the GP2 executable's strict schema. Each saved input/context pair must remain together at handoff. Replays retain prior failures and their provenance; a successful retry is a new recorded attempt, not erased history. Use the same observation snapshot/configuration on replay; changed live observations are a new run.

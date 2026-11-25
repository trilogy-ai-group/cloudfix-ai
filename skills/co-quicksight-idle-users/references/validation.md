# Validation: opt-out gate and the native check order

Read this at stage 3 of `guided.md`. It holds the opt-out gate, the eight native checks in order with their fail-fast behavior, and the protection rule.

## Opt-out gate (all input paths)

- Before marking any candidate eligible, enforce the exact **`cloudfix:dontFixIt`** opt-out across **CUR-discovered, ListUsers-discovered, explicitly selected, supplied-cost, recorded, replayed and retried** inputs.
- Use current authorized ListTagsForResource evidence or an authoritative supplied protection record; recorded-only runs use the recorded observation and make no live calls.
- A known nonempty string excludes the candidate from recommendations and totals: `"false"`, `"0"`, `"no"` and whitespace are nonempty, not boolean false. Do not trim or coerce.
- A successful complete tag response with no exact-key tag or exact empty string clears this gate for that observation.
- Failed, unsupported, missing, unattempted, invalid-type or incomplete tag evidence leaves protection unknown and blocks complete eligibility.
- Keep duplicate observations before map normalization: any unsuperseded nonempty value excludes; unresolved conflicting observations remain unknown unless exclusion is already established. An explicit authoritative supersession retains the earlier observation and its timestamp.
- Preserve historical CUR protection separately; a past CUR pass does not establish current clearance.

This is a **harness safety gate**, not a new native validation row. Native tags still normalize later as specified in `receiver-and-assets.md`. A swallowed tag exception is never evidence of clearance. Keep a native projection even when harness protection excludes it. Do not remove protected/opted-out users from the full native activity scan or receiver algorithm; receiver suitability is a separate change prerequisite.

## Native validation order

Initialize the projection with undecided `approved`, `stopProcessing:false`, `annualSavings:0`, `finderReportData:{}`, `otherImpactedNodeIds:[]`. A fail-fast failure sets rejected/stopped; later validation stages omit that item. Ordinary failure sets rejected but continues. Passing never undoes a prior failed row. Record missing/skipped checks outside native-shaped rows; an unperformed check is not a pass.

| Order | Native name / type | Exact rule and row value |
|---|---|---|
| 1 | `RecommendationFinalized` / Other | `{resourceId}`; pass iff complete supplied framework evidence has no record with this resource ID and finder ID where **`savingsFinalizedAt > asOf - 14 days`**. This is a strict timestamp lower bound, preserving time of day: equality does not match; future timestamps match; no status predicate or upper bound is applied. A prefiltered ID set requires provenance for that exact complete lookup. Ordinary failure. Use only explicitly supplied framework evidence; otherwise `not_evaluated`. No CloudFix lookup is required or permitted by this skill. |
| 2 | `ResourceExists` / Resource Exists | `{resourceId}`; normalized ARN map contains the resource. Fail-fast. The source uses ResourceExists, **not ResourceNotDeleted**: absence is resource-deleted classification, not proof of a manually completed deletion. |
| 3 | `ValidateUserRole` / Other | `{role}`; role exactly one of **AUTHOR, ADMIN, AUTHOR_PRO, ADMIN_PRO, READER_PRO**. Fail-fast. Ordinary READER is excluded. |
| 4 | `UserNotProtected` / Other | `{userName,protectedUsersPattern}`; fail-fast when a configured regex matches. Details below. |
| 5 | `UserNotActive` / Other | Activity summary from steps 4–5, plus `{windowDays,reason}`. Only reason `no-activity-identified` passes. Fail-fast. |
| 6 | `HasReceiver` / Other | `{receiverArn}` if a receiver exists, otherwise `{resourceId}`; existence required. Fail-fast. See step 6. |
| 7 | `MinimalSavings` / Savings Threshold | `{annualSavings,annualCost,minimalSavings,reason}`; annualSavings **>** configured minimum. Ordinary failure, after parameter reads and savings assignment. |
| 8 | `MaximumSavings` / Savings Threshold | `{annualSavings,annualCost,reason}`; annualSavings **<=** annualCost. Ordinary failure. |

In this table "steps 4–5" are the activity reads in `collection.md` and the aggregation and `UserNotActive` priority in `coverage-and-cache.md`; "step 6" is the receiver selection in `receiver-and-assets.md`. Rules 7 and 8 depend on savings and complete in `savings.md`.

Protection uses the full UserName and JavaScript `new RegExp(originalPattern,'i')` if the pattern is truthy and its trimmed form is nonempty. Do not trim the compiled pattern, add anchors or apply it to just the final username component. When UserName and compiled regex are present, the row contains regex `.source` and the test result. Missing either means no match and omits `protectedUsersPattern`. An invalid regex is logged and **ignored by native**, so this row passes; preserve that projection and the invalid configuration. The assessment must flag unresolved protection intent as `needs_evidence`, not claim valid protection or quietly repair the regex.

Python's `re` and a JavaScript `RegExp` do not read every pattern alike: with `\badmin\b`, JavaScript protects `éadmin` and Python's Unicode `\b` does not. The script compiles the pattern with ASCII classes and ASCII case folding, as JavaScript without the `u` flag does, and evaluates it only where the two engines agree: ASCII literals, punctuation escapes, `\d \w \s` and their negations, `\b`, `. ^ $ |`, plain, `(?:`, `(?=` and `(?!` groups, classes of literals and letter or digit ranges, and one quantifier per item. Any other pattern (a non-ASCII character, `\B`, a lookbehind, a named group, a backreference), and any UserName that holds a line end, a non-ASCII space or a character outside the Basic Multilingual Plane, leaves `UserNotProtected` `not_evaluated` and the row `needs_evidence`. A user is never recommended on a guess at the finder's answer.

Even after selected resources fail these checks, native activity collection still scans **every normalized allowed-role user**, including protected users and users absent from the selected resource/cost list. This full inventory is necessary to choose a receiver. Ordinary READER users are neither activity candidates nor receiver candidates. Do not limit this scan to selected idle candidates.

# Collection and normalization rules

Read this in stage 2 of `SKILL.md`, before interpreting the first `GetDistributionConfig`, `GetCachePolicy` or `ListTagsForResource` response. `SKILL.md` names the reads and their parameters; this file says what a usable response must contain and how it is normalized. "Native" and "the source" mean the CloudFix finder whose rules this skill reproduces.

## Distribution configuration

`GetDistributionConfig` requires a non-nullish `DistributionConfig` and non-nullish fields `CallerReference`, `Origins`, `DefaultCacheBehavior`, `Comment`, `Enabled`. Empty Comment and false Enabled are present. These are shallow requirements; do not add nested validation rules or an Enabled=true condition. Only the SDK's **NoSuchDistribution** error means absent. Empty/incomplete successful output or another error throws; it must never become `ResourceExists:false`. Keep the original response/diagnostics and identify the native batch-failure outcome. A missing supplied read is an evidence gap, not a fabricated SDK exception.

## Cache policies

The source requires non-nullish `CachePolicy.Id`, `LastModifiedTime`, `CachePolicyConfig`, and config `Name`, `MinTTL`. `DefaultTTL` and `MaxTTL` may be missing. A recorded unsuccessful/malformed required policy response produces the per-item exception described in `references/checks.md`, never an empty policy list. A missing unattempted response leaves the condition unevaluated.

## Tags

Normalize tags from `Tags.Items ?? []`, dropping entries without non-nullish Key. Base conversion copies only entries with both Key and Value present; empty strings are present and the last duplicate wins. Assign that record to the native field **`resourceTags`**, never a renamed `tags` field. A native tagging exception is swallowed into `[]`; preserve that projection **and** its error/unknown, without claiming that no tags exist. Tagging uncertainty does not change the compression or cost predicates. Optional account-owner/l4/l5 virtual tags require actual platform context and otherwise remain unknown.

In `assessment.json` the normalized record is `details.resourceTags`. When the tag read failed, set it to null, keep the error in the evidence file and add an `issues` entry of kind `qualification`; the decision does not change.

## Derived wrapper errors

For a supplied successful response that fails the required normalization shape, the wrapper's deterministic error is known: missing `CachePolicy` throws `Error` with message `No CachePolicy found with params <JSON.stringify(policyId)>`; missing a required policy field or config field throws `Error` with message `No complete CachePolicy found with params <JSON.stringify(policyId)>`. The stringified ID includes its JSON quotes. An incomplete distribution response throws `Error` with message `GetDistributionConfig for <distributionId> returned an incomplete distribution config`. Compute these messages from the offending response and ID; do not treat them as an observed service error. Keep the raw response and mark the wrapper error as derived. Runtime stack remains unavailable unless supplied.

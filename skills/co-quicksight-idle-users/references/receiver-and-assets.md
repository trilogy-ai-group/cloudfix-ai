# Receiver, assets and native tags

Read this at stage 3 of `SKILL.md` for check 6 (`HasReceiver`) and the reads that populate the proposed parameters. These reads describe a proposal; nothing here grants a permission or deletes a user.

## Receiver

Receiver is the **last** entry after stable ascending sort by eventTime of all activity-map entries with a present eventTime. Ties select the later map entry. Completion is not required when an event exists. Protected users can be receivers; ordinary readers cannot. Do not choose a preferred admin instead of this algorithm. An idle candidate still requires a receiver when its asset lists would be empty.

When a receiver exists, HasReceiver assigns `fixerParameters:{fromPrincipalId:<candidate PrincipalId>,toUserArn:<receiver ARN>}` and replaces report data with `{userReceivesResources:<receiver UserName or "">}`. Whether receiver exists or fails, this stage then merges **`annualCost`, `annualSavings:annualCost`, `userName:UserName??""`, `role`, `daysForIdleUser`** into `finderReportData`. Earlier fail-fast items never reach this report-population stage. Finalize technical validation: no recorded failed row means native approval; an unknown framework row must not be invented as passed. A standalone technical candidate may collect equivalent parameter evidence while retaining unknown full-framework approval separately.

## Assets

For technically approved/non-rejected candidates, collect all asset-search pages with AwsAccountId and one filter `{Name:<below>,Value:<candidate ARN>,Operator:"StringEquals"}`:

- SearchAnalyses and SearchDashboards: `QUICKSIGHT_USER`.
- SearchDataSets and SearchDataSources: `DIRECT_QUICKSIGHT_SOLE_OWNER`.

Keep all returned records. Analyses retain present AnalysisId only when Status is not `DELETED`; dashboards, datasets and data sources require their corresponding ID only. Missing/null IDs are excluded from native arrays, empty strings are present, and duplicates are retained. Assign all four arrays `analyses`, `dashboards`, `dataSets`, `dataSources` to fixerParameters. These search filters describe source selection; do not broaden asset ownership claims. A search exception is a native per-item `Exception` fail-fast, not an empty list. Missing unattempted reads remain unevaluated rather than fabricated exceptions.

## Native tags

Read `ListTagsForResource({ResourceArn:<candidate ARN>})`, reusing the complete observation already collected for the all-path opt-out gate when it is the same snapshot. Native drops tags missing/null Key or Value, defaults successful absent Tags to `[]`, and swallows read exceptions to undefined. Tag record uses present pairs with last duplicate winning; a swallowed exception produces `{}` natively but remains unknown/error evidence in the assessment. After successful parameter population set `resourceFixedConfiguration:{user:"deleted"}`; this is a proposed configuration, not execution status. Native population reads assets and tags concurrently. Optional CloudFix account-owner/l4/l5 virtual tags require supplied context; never invent or write tags.

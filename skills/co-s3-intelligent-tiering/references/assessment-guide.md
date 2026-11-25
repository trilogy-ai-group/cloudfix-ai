# Assessment guide: S3 Intelligent-Tiering

Read this in stage 1 of `guided.md` for the evidence file names and the result shape, and use it again in stage 6, before saving `assessment.json` and writing the customer report. It stands alone: this skill's fixed values, what each field means here, one filled example, the totals rule and the report layout. The schema is `assessment-v1.schema.json` in this folder. An omitted or null value means unknown. Never write `0` for unknown.

## Contents

- Fixed values
- What the fields mean here
- Decisions
- Costs
- Filled example
- Keeping the file small
- Totals
- Customer report

## Fixed values

Use these literals exactly. Do not invent other names.

**Evidence files.** `evidence/run-metadata.json` (the run's scope, clock and request, as `{run_metadata: {skill_revision, accountId, region, asOf, evaluation_timezone, configuration, run_classification, public_price_fallback_allowed}, request: {pricing_mode, resource_ids, allow_public_fallback, profile, billing_profile, workers}}`), then one file per read type, each a list of read records in request order, one per request, page or failure, so that `evidence/lifecycle.json#/3` is one record, `{service, operation, region, profile, input, observed_at, output}` with `error` in place of `output` for a failed read (the account is in the identity record and `run-metadata.json`): `evidence/identity.json`, `evidence/list-buckets.json`, `evidence/tagging.json`, `evidence/lifecycle.json`, `evidence/intelligent-tiering.json`, `evidence/metrics.json` (every `GetMetricData` request and page) and, in Cost Explorer `billing`, `evidence/cost-explorer.json` (the billing identity first, then both Cost Explorer requests with every page); `evidence/economics.json` (`supplied`: the supplied rows with provenance; `cost_explorer_effective_rate`: the window, the usage types returned with each mapping or exclusion reason, the per-class costs, usage and rates and the references of the saved requests; `catalog`: the public catalog as retrieved, with URL, time, hash, version and the selected SKUs and price dimensions, or the URL, time and `error` (`code`, `message`) of a read that failed; or the SQL, mapping, execution ID and every result page) and `evidence/calculations.json` (per row: the metric reductions, the economic row and the fee estimate, unrounded; plus `metric_queries`, the ID-to-bucket/series map of every metrics request). A replay with `scripts/assess.py --replay` needs `run-metadata.json` and answers each read from the saved record with the same service, operation, region and `input`, so save each `input` exactly as `references/collection.md` and `references/billing-cost-explorer.md` give it, with the `USAGE_TYPE` values of `GetCostAndUsage` in ascending order.

| Value | Literal for this skill |
| --- | --- |
| `schema_version` | `"1.0"` |
| `skill.id` | `co-s3-intelligent-tiering` |
| `skill.revision` | The `metadata.version` in the `SKILL.md` frontmatter |
| `run.mode` | `local_code` when `scripts/assess.py` produced the result, `guided` on the guided path |
| `resource.type` | `AWS::S3::Bucket` |
| `resource.id` | The bucket name |
| `findings[].id` | The bucket name. Only when one bucket has several billing rows, which only a CUR export or supplied rows can produce: `<bucket>#<n>`, where `n` is the row's 1-based position in the saved economics result |
| `recommendation.action` | `transition_to_intelligent_tiering` |
| `recommendation.parameters` keys | `resourceId` and `bucketName` (both the bucket name) and `sitRuleName` (always `CloudFix-Standard-To-Sit`) |
| `execution_status` | `not_executed` |

Check names, in the finder's order. A recommended row lists all twelve; a row that stopped earlier ends at the check that stopped it, as `checks` below defines:

1. `RecommendationFinalized`
2. `ResourceExists`
3. `LifecycleConfigurationReadable`
4. `ResourceFixed`
5. `ControlTower`
6. `NoEarlyExpirationRule`
7. `NoEarlyAsyncGlacierTransition`
8. `NoProblematicIntelligentTieringConfiguration`, or `IntelligentTieringConfigurationNotChecked` when the Intelligent-Tiering configuration read was denied
9. `HasObjectMetrics`
10. `NetSavingsAfterObjectFees`
11. `MinimalSavings`
12. `MaximumSavings`

Pricing modes. Each economics mode of stage 1 of `guided.md` fixes the price source and method of every calculated row:

| Requested economics | `scope.pricing_mode` | `costs.basis` | `costs.estimation_method` | What `current_annual` is |
| --- | --- | --- | --- | --- |
| Caller-supplied economic rows (`supplied-native-resource`) | `supplied` | `caller_supplied` | `model_estimate` | The supplied `annualCost` |
| Billed costs through Cost Explorer (`cost-explorer`), the default for a billed-cost request | `billing` | `account_effective_rate` | `model_estimate` | The account-and-region effective rate of each class, applied to the bucket's 7-day mean Standard and Standard-IA size for 12 months. Not the bucket's bill. `source_period` is the Cost Explorer window, end exclusive |
| A resource-level billing export the customer supplied (`cur`) | `billing` | `resource_billed` | `model_estimate` | The billing row's `annualCost`: seven billed days, annualized. `source_period` is the row's `start_date` and `end_date` |
| Current AWS public prices (`public-price-model`) | `public_prices` | `public_price` | `model_estimate` | Standard plus Standard-IA payload storage at public rates. Not the bucket's bill |
| No economics (`qualitative`) | `technical_only` | none | none | Not calculated |

One assessment has one pricing mode and one basis. A comparison on a second basis is a second `assessment.json` in its own folder; never mix two price sources in one file or one total.

A billed-cost request is `billing` and stays `billing` when its read fails: the affected rows are `error` or `needs_evidence`, never rows at public prices. The schema rejects a `public_price` row in a `billing` assessment. The one exception is stage 1's **conditional request**, saved as `references/billing-cost-explorer.md` states.

Every calculated row is a `model_estimate`, never a `configuration_projection`. The saving is not the difference between two priced configurations: it depends on which Intelligent-Tiering tiers the objects settle in, and nobody measured that. A `model_estimate` must state its assumptions. State them once, in the top-level `qualifications`:

- **Tier mix.** The future tier mix is modeled with the finder's fixed coefficients: after the transition, storage is assumed to cost 0.4185 of the Standard price, with Standard-IA weighted at 0.65 of Standard. Object access patterns were not measured. In `supplied` mode the gross saving is taken as supplied; say whose estimate it is.
- **Object fees.** Savings are after the lower of two bounds, the monitoring fee or the loss on objects below 128 KiB estimated from the average object size. The fee rates are fixed USD constants, not live prices.
- **First year.** The one-time transition charge covers existing objects only; later arrivals incur further charges.
- **Price source.** Cost Explorer `billing`: the assumptions `references/billing-cost-explorer.md` lists. `billing` from a CUR export: seven billed days annualized, even where coverage is partial; retrieval and early-delete charges billed in that window are counted as saved; billing and CloudWatch timestamps differ. `public_prices`: payload storage only, each bucket quoted alone, no discounts, no existing-monitoring credit and no retrieval or early-delete saving.

In `public_prices` mode and in Cost Explorer `billing` the modeled row is the row's economic row, and orders 9 to 12 are evaluated on it; a recommended row is then a modeled candidate for the included payload only. `technical_only` never produces a recommendation or a saving: orders 10 to 12 are the finder's own gates and cannot run without economics.

## What the fields mean here

- **`run.status`.** `complete`: every bucket in scope has a final decision, which includes rejections and a completed empty listing. `partial`: something in scope is unresolved. `failed`: an error prevented the assessment, such as a failed identity read or bucket listing. A `technical_only` run is `partial` unless every bucket was rejected.
- **`scope`.** `account_id` is the account from the identity read, null only if identity could not be established. `regions` holds the requested region or regions. `resource_ids` is the explicit bucket selection, or null when the run assessed every bucket in the regional listing (with a CUR export, plus any bucket the CUR query returned that the listing no longer holds). `pricing_mode` is what the customer asked for; a failure never changes it. A conditional request is saved under the basis that produced the result.
- **`coverage`.** Counts distinct buckets in scope, not rows. A bucket with two billing rows counts once. `inventory_complete` is true when every listing page was read. When a page is missing, a bucket that the pages read do not hold is `needs_evidence`, never a `ResourceExists` failure. `assessed_count` counts buckets whose rows are all `recommended` or `not_recommended`; `unassessed_ids` names the rest. Unknown counts are null. The listing, tagging and metric reads are evidence, not coverage.
- **Rows.** One row per economic row: one supplied row, one CUR result row, or one bucket in Cost Explorer `billing`, `public_prices` and `technical_only`. A bucket in scope with no economic row still gets one row. Never merge two billing rows of one bucket.
- **`checks`.** The finder's checks in order from order 1, each `pass`, `fail` or `not_evaluated`. `value` is the finder's value object for that check, as observed. Anything but `pass` carries a `reason`. A check whose input is missing and `IntelligentTieringConfigurationNotChecked` are `not_evaluated`. A fault thrown inside a check makes that check `not_evaluated` with the error as its reason, and the row keeps the fault as `details.exception` (`context`, `error`, and `errorName` and `stack` only when recorded).
- **Where a row's `checks` end.** A row lists its checks up to and including the one that stopped it, and nothing after. Three things stop a row: a fail-fast failure (orders 2, 3, 4, 5, 9 and 10), a fault thrown inside a check, and the first check that cannot run because a required input is missing: order 2 when a listing page is missing and the pages read do not hold the bucket, order 3 when the lifecycle read failed or was not supplied, order 8 when the Intelligent-Tiering read failed (not denied) or was not supplied, and order 9 on a row that has no economic row and no completed metric rejection. That check is the last entry, with its `fail` or its `not_evaluated` reason. Every later check is by this definition `not_evaluated`, not run after the row stopped, and is not written out one by one. A reader must never take an absent check for a pass. Three things do not stop a row, so its list continues: an ordinary failure (orders 1, 6, 7, 11 and 12), order 1 left `not_evaluated` because CloudFix history was not supplied, and order 8 as `IntelligentTieringConfigurationNotChecked`. A recommended row therefore always lists all twelve, and so does a row that only ordinary failures rejected.
- **`technical_eligibility`.** Omit it on `recommended` rows. Set it to `eligible` only on a `not_recommended` row that only savings checks rejected (orders 10 to 12); leave it out otherwise.
- **`recommendation`.** On recommended rows only: the fixed `action` and `parameters`. Other rows have none.
- **`costs`.** See the table below. Keep unrounded values and negatives. A current cost is known when it needs no further read: a supplied or billed row naming the bucket, or the resolved public prices applied to its measured sizes. A row with a known current cost carries `current_annual`, `basis` and `currency` even when it is `not_calculated`, including a row stopped by an earlier check; a row with no known current cost carries `{"status": "not_calculated"}` only.
- **`qualifications`.** Run-wide limits once at the top level. On a row, only what is specific to that bucket: an archive-access warning, an unchecked tagging or Intelligent-Tiering configuration read, a live `dont_fix_it` tag, and lifecycle rules outside the bucket-wide checks. A statement that is true of every row, or of every row with one decision, is run-wide: never repeat it on rows, and leave `qualifications` off a row that has nothing of its own.
- **`evidence_refs`.** `path#/json/pointer`, relative to `run.evidence_root`, by default the folder holding `assessment.json`. Required on recommended rows. Use the top-level list for the identity read, the bucket listing and run-wide price evidence.
- **`issues`.** What is missing, failed or worth flagging. Every `needs_evidence` or `error` row has one, and its `request` names the exact next read or input. A missing economic row, or a supplied row without a usable required amount (the issue's message names the row and the amounts), asks for `{"kind": "input", "name": "economics"}`, except a missing Cost Explorer billed size, which asks for the `GetMetricData` read that `references/collection.md` names. A failed or missing bucket read gives one issue for each bucket it affects, with its `resource_id`, and comes before any economics issue on that row. A missing listing page or an economics failure is one issue without a `resource_id`.
- **`verification`.** `method` is `self_check`, `none`, or `independent_review` only when a separate reviewer actually examined the evidence. `summary` says what was and was not checked.
- **`details`.** `tier_sizes_gb` (the seven tier sizes: `standardGb`, `standardIaGb`, `onezoneIaGb`, `intelligentTieringGb`, `glacierIrGb`, `glacierGb`, `deepArchiveGb`, plus `standardGbBilled` and `standardIaGbBilled` in Cost Explorer `billing`) and `billing_row` (`position`, `resourceName`, `tag_original_resource_id`) when a bucket has several billing rows. The economic row (the row as used: the supplied row, the mapped billing row, or the modeled row) is not repeated in `details`: it is the `economic_row` of the row's record in `evidence/calculations.json`, which the row's `evidence_refs` name. A supplied row is saved there as parsed, with `missingFields` (numeric fields that were absent or null) and `malformedFields` (present, but `Number.parseFloat` reads no number) naming the fields that hold a normalized zero. The complete fee estimate stays in the evidence file; its amounts already appear in the values of checks 9 and 10. In `public_prices`, the `modeled_checks` that `references/public-price-model.md` names are the row's `checks`, and its `public_price_model` record is the `catalog` entry of `evidence/economics.json`. Caller metadata goes in a top-level `run_context`, and an explicitly requested experiment in a top-level `experiment`.
- **`source_output`.** Leave it out unless the user explicitly asked for a native-shaped projection and it was saved.

## Decisions

Apply in order:

1. A completed check that failed gives `not_recommended`, whether it was fail-fast or ordinary, and even if other reads are missing. Keep its reason and any calculated costs. An unreadable lifecycle configuration is such a rejection (`LifecycleConfigurationReadable`); also add an issue of kind `read_error`, so the customer can see that access caused it.
2. Otherwise, a required read or calculation that failed gives `error`: a thrown fault, a per-bucket metric fault, a failed or denied billing read (Cost Explorer or the CUR query) or catalog read in a priced mode. A required input that is absent gives `needs_evidence`: the bucket has no economic row, because none was supplied, the CUR query returned none, Cost Explorer returned no usable usage for a storage class the bucket holds, the public-price or effective-rate model is undefined for it, or the run is `technical_only`. A supplied row whose `annualCost`, `totalSavings`, `dataSavings`, `stdGbMonths` or `iaGbMonths` is in `missingFields` or `malformedFields` is such an absent input: no check or cost reads its zeros. So is a replay that holds no `GetBucketTagging` record for the bucket.
3. Only a row whose checks all passed, with calculated savings, is `recommended`. Two checks may be `not_evaluated` on a recommended row, because the finder does not reject on them: order 1 when CloudFix history was not supplied, and order 8 when it is `IntelligentTieringConfigurationNotChecked`. Both stay stated qualifications, never a fabricated pass. A bucket with several rows has a `recommended` row only when none of its rows is `needs_evidence` or `error`; otherwise its passing rows are `needs_evidence` too, keep their checks and costs, and are not totaled.

Earlier versions of this skill labelled rows `candidate`, `rejected`, `insufficient_evidence` and `read_error`. Those are `recommended`, `not_recommended`, `needs_evidence` and `error`.

## Costs

| Situation | `costs` |
| --- | --- |
| Fee estimate saved (metric coverage passed) | `status: "calculated"`, with `current_annual` = `annualCost`, `annual_savings` = `netSavings`, `target_annual` = `annualCost - annualSavings`, `one_time_cost` = `transitionCharge`, `first_year_savings` = `firstYearNetSavings`, plus `basis`, `estimation_method` and `currency`. This holds for a row that a savings check then rejected. |
| Economic row but no fee estimate (stopped before order 9, or order 9 failed) | `status: "not_calculated"`, with `current_annual`, `basis` and `currency` kept and no savings amount. The finder's gross figure for such a row is not a saving: keep it only as `details.nativeCalculation`, or `details.modeledCalculation` in `public_prices` and in `billing` from Cost Explorer, with `annualSavings` and its `origin`. |
| No economic row, `technical_only`, or a failed economics read | `{"status": "not_calculated"}` |

`annual_savings` is the steady-state saving after the recurring monitoring fee. `one_time_cost` is the initial transition charge for existing objects. `currency` is `USD`; a supplied file in another currency is refused. In `billing` every row with an amount also carries `source_period`: `{"start": "<Start>T00:00:00Z", "end": "<End>T00:00:00Z"}` for the Cost Explorer window, or the CUR row's own dates.

## Filled example

A `public_prices` run over a regional listing of four buckets, as `scripts/assess.py` writes it; the guided path produces the same shape with `"mode": "guided"`. Two buckets are recommended, one is rejected by the 50 USD minimum with its calculated costs kept, and one is rejected by a fail-fast check at order 4, where its `checks` end. All values are illustrative: the placeholder catalog prices Standard at 0.02 and Standard-IA at 0.01 USD per GB-month in one flat band. A Cost Explorer `billing` run has the same shape, with `"pricing_mode": "billing"`, `"basis": "account_effective_rate"` on each row and in `totals`, a `source_period` in each row's `costs`, `"basis": "cost-explorer-effective-rate"` in the economic row, and `evidence/economics.json#/cost_explorer_effective_rate` in the top-level `evidence_refs`.

```json
{
  "schema_version": "1.0",
  "skill": {
    "id": "co-s3-intelligent-tiering",
    "revision": "0.5.0",
    "source_ref": null
  },
  "run": {
    "observed_at": "2026-11-01T00:00:00.000Z",
    "mode": "local_code",
    "status": "complete"
  },
  "scope": {
    "account_id": "000000000000",
    "regions": [
      "us-east-1"
    ],
    "resource_ids": null,
    "pricing_mode": "public_prices"
  },
  "coverage": {
    "inventory_complete": true,
    "discovered_count": 4,
    "assessed_count": 4,
    "unassessed_ids": []
  },
  "run_metadata": {
    "skill_revision": null,
    "accountId": "000000000000",
    "region": "us-east-1",
    "asOf": "2026-11-01T00:00:00.000Z",
    "evaluation_timezone": "UTC",
    "configuration": {
      "minimalSavings": 50,
      "minTransitionDays": 31
    },
    "run_classification": "standard",
    "public_price_fallback_allowed": false
  },
  "findings": [
    {
      "id": "example-logs",
      "resource": {
        "id": "example-logs",
        "type": "AWS::S3::Bucket",
        "region": "us-east-1"
      },
      "decision": "recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history was not queried."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "example-logs"
          }
        },
        {
          "name": "LifecycleConfigurationReadable",
          "status": "pass",
          "value": {
            "resourceId": "example-logs"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "globalRuleCount": 0
          }
        },
        {
          "name": "ControlTower",
          "status": "pass",
          "value": {
            "bucketName": "example-logs"
          }
        },
        {
          "name": "NoEarlyExpirationRule",
          "status": "pass",
          "value": {
            "earlyExpirationRuleCount": 0,
            "earlyExpirationRuleIds": [],
            "minExpirationDays": 31
          }
        },
        {
          "name": "NoEarlyAsyncGlacierTransition",
          "status": "pass",
          "value": {
            "earlyTransitionRuleCount": 0,
            "earlyTransitionRuleIds": [],
            "minTransitionDays": 31
          }
        },
        {
          "name": "NoProblematicIntelligentTieringConfiguration",
          "status": "pass",
          "value": {
            "intelligentTieringConfigurationCount": 0,
            "enabledArchiveAccessTiers": []
          }
        },
        {
          "name": "HasObjectMetrics",
          "status": "pass",
          "value": {
            "metricGaps": [],
            "objectCount": 2000000,
            "archivedObjectCount": 0,
            "totalSizeGb": 5000.0,
            "transitionableSizeGb": 5000.0,
            "monitoredObjectCount": 0.0,
            "candidateObjectCount": 2000000.0,
            "curDataSavingsUsd": 594.0,
            "curStdGbMonths": 4000.0,
            "curIaGbMonths": 1000.0
          }
        },
        {
          "name": "NetSavingsAfterObjectFees",
          "status": "pass",
          "value": {
            "transitionableSizeGb": 5000.0,
            "candidateObjectCount": 2000000.0,
            "averageObjectSizeKb": 2621.44,
            "minObjectSizeKb": 128,
            "smallObjectFraction": 0.048828125,
            "annualMonitoringFeeUsd": 60.00000000000001,
            "annualNetSavingsUsd": 534.0,
            "transitionChargeUsd": 20.0,
            "firstYearNetSavingsUsd": 514.0
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 534.0,
            "annualCost": 1080.0,
            "minimalSavings": 50
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 534.0,
            "annualCost": 1080.0
          }
        }
      ],
      "recommendation": {
        "action": "transition_to_intelligent_tiering",
        "parameters": {
          "resourceId": "example-logs",
          "bucketName": "example-logs",
          "sitRuleName": "CloudFix-Standard-To-Sit"
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "model_estimate",
        "currency": "USD",
        "current_annual": 1080.0,
        "target_annual": 546.0,
        "annual_savings": 534.0,
        "one_time_cost": 20.0,
        "first_year_savings": 514.0
      },
      "evidence_refs": [
        "evidence/tagging.json#/0",
        "evidence/lifecycle.json#/0",
        "evidence/intelligent-tiering.json#/0",
        "evidence/metrics.json#/0",
        "evidence/metrics.json#/1",
        "evidence/economics.json#/catalog",
        "evidence/calculations.json#/example-logs"
      ],
      "details": {
        "tier_sizes_gb": {
          "standardGb": 4000.0,
          "standardIaGb": 1000.0,
          "onezoneIaGb": 0.0,
          "intelligentTieringGb": 0.0,
          "glacierIrGb": 0.0,
          "glacierGb": 0.0,
          "deepArchiveGb": 0.0
        }
      }
    },
    {
      "id": "example-media",
      "resource": {
        "id": "example-media",
        "type": "AWS::S3::Bucket",
        "region": "us-east-1"
      },
      "decision": "recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history was not queried."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "example-media"
          }
        },
        {
          "name": "LifecycleConfigurationReadable",
          "status": "pass",
          "value": {
            "resourceId": "example-media"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "globalRuleCount": 0
          }
        },
        {
          "name": "ControlTower",
          "status": "pass",
          "value": {
            "bucketName": "example-media"
          }
        },
        {
          "name": "NoEarlyExpirationRule",
          "status": "pass",
          "value": {
            "earlyExpirationRuleCount": 0,
            "earlyExpirationRuleIds": [],
            "minExpirationDays": 31
          }
        },
        {
          "name": "NoEarlyAsyncGlacierTransition",
          "status": "pass",
          "value": {
            "earlyTransitionRuleCount": 0,
            "earlyTransitionRuleIds": [],
            "minTransitionDays": 31
          }
        },
        {
          "name": "NoProblematicIntelligentTieringConfiguration",
          "status": "pass",
          "value": {
            "intelligentTieringConfigurationCount": 0,
            "enabledArchiveAccessTiers": []
          }
        },
        {
          "name": "HasObjectMetrics",
          "status": "pass",
          "value": {
            "metricGaps": [],
            "objectCount": 400000,
            "archivedObjectCount": 0,
            "totalSizeGb": 2000.0,
            "transitionableSizeGb": 2000.0,
            "monitoredObjectCount": 0.0,
            "candidateObjectCount": 400000.0,
            "curDataSavingsUsd": 279.12,
            "curStdGbMonths": 2000.0,
            "curIaGbMonths": 0.0
          }
        },
        {
          "name": "NetSavingsAfterObjectFees",
          "status": "pass",
          "value": {
            "transitionableSizeGb": 2000.0,
            "candidateObjectCount": 400000.0,
            "averageObjectSizeKb": 5242.88,
            "minObjectSizeKb": 128,
            "smallObjectFraction": 0.0244140625,
            "annualMonitoringFeeUsd": 12.000000000000002,
            "annualNetSavingsUsd": 267.12,
            "transitionChargeUsd": 4.0,
            "firstYearNetSavingsUsd": 263.12
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 267.12,
            "annualCost": 480.0,
            "minimalSavings": 50
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 267.12,
            "annualCost": 480.0
          }
        }
      ],
      "recommendation": {
        "action": "transition_to_intelligent_tiering",
        "parameters": {
          "resourceId": "example-media",
          "bucketName": "example-media",
          "sitRuleName": "CloudFix-Standard-To-Sit"
        }
      },
      "qualifications": [
        "1 enabled lifecycle rule outside the bucket-wide checks (tmp-cleanup: prefix tmp/, expires after 7 days). Not tested as safe; review before any change."
      ],
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "model_estimate",
        "currency": "USD",
        "current_annual": 480.0,
        "target_annual": 212.88,
        "annual_savings": 267.12,
        "one_time_cost": 4.0,
        "first_year_savings": 263.12
      },
      "evidence_refs": [
        "evidence/tagging.json#/1",
        "evidence/lifecycle.json#/1",
        "evidence/intelligent-tiering.json#/1",
        "evidence/metrics.json#/0",
        "evidence/metrics.json#/1",
        "evidence/economics.json#/catalog",
        "evidence/calculations.json#/example-media"
      ],
      "details": {
        "tier_sizes_gb": {
          "standardGb": 2000.0,
          "standardIaGb": 0.0,
          "onezoneIaGb": 0.0,
          "intelligentTieringGb": 0.0,
          "glacierIrGb": 0.0,
          "glacierGb": 0.0,
          "deepArchiveGb": 0.0
        }
      }
    },
    {
      "id": "example-small",
      "resource": {
        "id": "example-small",
        "type": "AWS::S3::Bucket",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history was not queried."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "example-small"
          }
        },
        {
          "name": "LifecycleConfigurationReadable",
          "status": "pass",
          "value": {
            "resourceId": "example-small"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "globalRuleCount": 0
          }
        },
        {
          "name": "ControlTower",
          "status": "pass",
          "value": {
            "bucketName": "example-small"
          }
        },
        {
          "name": "NoEarlyExpirationRule",
          "status": "pass",
          "value": {
            "earlyExpirationRuleCount": 0,
            "earlyExpirationRuleIds": [],
            "minExpirationDays": 31
          }
        },
        {
          "name": "NoEarlyAsyncGlacierTransition",
          "status": "pass",
          "value": {
            "earlyTransitionRuleCount": 0,
            "earlyTransitionRuleIds": [],
            "minTransitionDays": 31
          }
        },
        {
          "name": "NoProblematicIntelligentTieringConfiguration",
          "status": "pass",
          "value": {
            "intelligentTieringConfigurationCount": 0,
            "enabledArchiveAccessTiers": []
          }
        },
        {
          "name": "HasObjectMetrics",
          "status": "pass",
          "value": {
            "metricGaps": [],
            "objectCount": 50000,
            "archivedObjectCount": 0,
            "totalSizeGb": 200.0,
            "transitionableSizeGb": 200.0,
            "monitoredObjectCount": 0.0,
            "candidateObjectCount": 50000.0,
            "curDataSavingsUsd": 27.912,
            "curStdGbMonths": 200.0,
            "curIaGbMonths": 0.0
          }
        },
        {
          "name": "NetSavingsAfterObjectFees",
          "status": "pass",
          "value": {
            "transitionableSizeGb": 200.0,
            "candidateObjectCount": 50000.0,
            "averageObjectSizeKb": 4194.304,
            "minObjectSizeKb": 128,
            "smallObjectFraction": 0.030517578125,
            "annualMonitoringFeeUsd": 1.5000000000000002,
            "annualNetSavingsUsd": 26.412,
            "transitionChargeUsd": 0.5,
            "firstYearNetSavingsUsd": 25.912
          }
        },
        {
          "name": "MinimalSavings",
          "status": "fail",
          "value": {
            "annualSavings": 26.412,
            "annualCost": 48.0,
            "minimalSavings": 50
          },
          "reason": "Annual savings after object fees ($26.41) are not above the 50 USD minimum."
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 26.412,
            "annualCost": 48.0
          }
        }
      ],
      "technical_eligibility": "eligible",
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "model_estimate",
        "currency": "USD",
        "current_annual": 48.0,
        "target_annual": 21.588,
        "annual_savings": 26.412,
        "one_time_cost": 0.5,
        "first_year_savings": 25.912
      },
      "evidence_refs": [
        "evidence/tagging.json#/2",
        "evidence/lifecycle.json#/2",
        "evidence/intelligent-tiering.json#/2",
        "evidence/metrics.json#/0",
        "evidence/metrics.json#/1",
        "evidence/economics.json#/catalog",
        "evidence/calculations.json#/example-small"
      ],
      "details": {
        "tier_sizes_gb": {
          "standardGb": 200.0,
          "standardIaGb": 0.0,
          "onezoneIaGb": 0.0,
          "intelligentTieringGb": 0.0,
          "glacierIrGb": 0.0,
          "glacierGb": 0.0,
          "deepArchiveGb": 0.0
        }
      }
    },
    {
      "id": "example-tiered",
      "resource": {
        "id": "example-tiered",
        "type": "AWS::S3::Bucket",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history was not queried."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "example-tiered"
          }
        },
        {
          "name": "LifecycleConfigurationReadable",
          "status": "pass",
          "value": {
            "resourceId": "example-tiered"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "fail",
          "value": {
            "globalRuleCount": 1,
            "intelligentTieringRuleId": "move-to-it"
          },
          "reason": "An enabled bucket-wide lifecycle rule (move-to-it) already transitions to Intelligent-Tiering."
        }
      ],
      "costs": {
        "status": "not_calculated",
        "basis": "public_price",
        "currency": "USD",
        "current_annual": 240.0
      },
      "evidence_refs": [
        "evidence/tagging.json#/3",
        "evidence/lifecycle.json#/3",
        "evidence/intelligent-tiering.json#/3",
        "evidence/metrics.json#/0",
        "evidence/metrics.json#/1",
        "evidence/economics.json#/catalog",
        "evidence/calculations.json#/example-tiered"
      ],
      "details": {
        "tier_sizes_gb": {
          "standardGb": 1000.0,
          "standardIaGb": 0.0,
          "onezoneIaGb": 0.0,
          "intelligentTieringGb": 0.0,
          "glacierIrGb": 0.0,
          "glacierGb": 0.0,
          "deepArchiveGb": 0.0
        },
        "modeledCalculation": {
          "annualSavings": 139.56,
          "origin": "resource.totalSavings gross fallback; not a net saving"
        }
      }
    }
  ],
  "totals": {
    "decision_counts": {
      "recommended": 2,
      "not_recommended": 2,
      "needs_evidence": 0,
      "error": 0
    },
    "annual_savings": 801.12,
    "currency": "USD",
    "basis": "public_price",
    "estimation_method": "model_estimate",
    "current_annual": 1560.0,
    "target_annual": 758.88
  },
  "issues": [],
  "qualifications": [
    "Model-based estimate. The future Intelligent-Tiering tier mix is modeled, not measured: storage is assumed to cost 0.4185 of the Standard price after the transition, with Standard-IA weighted at 0.65 of Standard. No object access pattern was measured.",
    "AWS public prices for Standard and Standard-IA payload storage only, quoted for each bucket alone at its current size. Current cost is that modeled storage cost, not the bucket's bill. Discounts, requests, retrievals, early deletes, minimum-size and minimum-duration charges and other storage classes are excluded; no existing-monitoring credit and no retrieval or early-delete saving is assumed.",
    "Savings are after the lower of two bounds: the monitoring fee (0.0025 USD per 1,000 objects per month) or the loss on objects below 128 KiB, estimated from the average object size. Objects below 128 KiB do not produce the modeled saving.",
    "First-year savings also subtract a one-time transition charge (0.01 USD per 1,000 objects) for existing objects only; objects that arrive later incur further charges.",
    "The object count is the latest daily value within 90 days and sizes are the first value returned within 3 days; they need not describe one instant.",
    "CloudFix recommendation history was not queried, so RecommendationFinalized is not evaluated. Buckets were discovered with ListBuckets, not by CloudFix billing discovery.",
    "Optional archive tiers, application retrieval needs and prefix-, tag- or noncurrent-version lifecycle rules were not assessed as safe. Nothing was changed.",
    "A row that stopped early lists the finder's checks up to the one that stopped it; the later checks were not run."
  ],
  "verification": {
    "method": "self_check",
    "summary": "Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. Every metric reduction, economic row and fee estimate is recorded in evidence/calculations.json. AWS facts were not re-read. No independent review took place."
  },
  "evidence_refs": [
    "evidence/run-metadata.json",
    "evidence/identity.json#/0",
    "evidence/list-buckets.json",
    "evidence/economics.json#/catalog"
  ],
  "execution_status": "not_executed"
}
```

A row that could not be resolved, and the issue that says why and what to read next: the same evidence with `GetMetricData` answering `Forbidden` for the first bucket's object-count query. The fault at order 9 stopped the row, so its `checks` end there:

```json
{
  "finding": {
    "id": "example-logs",
    "resource": {
      "id": "example-logs",
      "type": "AWS::S3::Bucket",
      "region": "us-east-1"
    },
    "decision": "error",
    "checks": [
      {
        "name": "RecommendationFinalized",
        "status": "not_evaluated",
        "reason": "CloudFix recommendation history was not queried."
      },
      {
        "name": "ResourceExists",
        "status": "pass",
        "value": {
          "resourceId": "example-logs"
        }
      },
      {
        "name": "LifecycleConfigurationReadable",
        "status": "pass",
        "value": {
          "resourceId": "example-logs"
        }
      },
      {
        "name": "ResourceFixed",
        "status": "pass",
        "value": {
          "globalRuleCount": 0
        }
      },
      {
        "name": "ControlTower",
        "status": "pass",
        "value": {
          "bucketName": "example-logs"
        }
      },
      {
        "name": "NoEarlyExpirationRule",
        "status": "pass",
        "value": {
          "earlyExpirationRuleCount": 0,
          "earlyExpirationRuleIds": [],
          "minExpirationDays": 31
        }
      },
      {
        "name": "NoEarlyAsyncGlacierTransition",
        "status": "pass",
        "value": {
          "earlyTransitionRuleCount": 0,
          "earlyTransitionRuleIds": [],
          "minTransitionDays": 31
        }
      },
      {
        "name": "NoProblematicIntelligentTieringConfiguration",
        "status": "pass",
        "value": {
          "intelligentTieringConfigurationCount": 0,
          "enabledArchiveAccessTiers": []
        }
      },
      {
        "name": "HasObjectMetrics",
        "status": "not_evaluated",
        "reason": "Validation error: GetMetricData returned Forbidden for this bucket."
      }
    ],
    "costs": {
      "status": "not_calculated"
    },
    "evidence_refs": [
      "evidence/tagging.json#/0",
      "evidence/lifecycle.json#/0",
      "evidence/intelligent-tiering.json#/0",
      "evidence/metrics.json#/0",
      "evidence/metrics.json#/1",
      "evidence/economics.json#/catalog",
      "evidence/calculations.json#/example-logs"
    ],
    "details": {
      "tier_sizes_gb": {
        "standardGb": 4000.0,
        "standardIaGb": 1000.0,
        "onezoneIaGb": 0.0,
        "intelligentTieringGb": 0.0,
        "glacierIrGb": 0.0,
        "glacierGb": 0.0,
        "deepArchiveGb": 0.0
      },
      "exception": {
        "context": "Validation error: ",
        "error": "GetMetricData returned Forbidden for this bucket."
      }
    }
  },
  "issue": {
    "kind": "read_error",
    "stage": "collection",
    "resource_id": "example-logs",
    "message": "GetMetricData returned Forbidden for this bucket.",
    "request": {
      "kind": "aws_read",
      "name": "GetMetricData",
      "service": "cloudwatch",
      "region": "us-east-1"
    },
    "evidence_refs": [
      "evidence/metrics.json#/0",
      "evidence/metrics.json#/1"
    ]
  }
}
```

Such a run is `partial`, lists `example-logs` in `coverage.unassessed_ids`, and its total is a subtotal for the buckets assessed.

## Keeping the file small

An account can hold hundreds of buckets, and most are usually rejected. A rejected row needs its decision, the checks that ran, its reason and its evidence references; it does not need text that is the same on every row. Without dropping any fact from a recommended row:

- State every run-wide fact once in the top-level `qualifications`: the model assumptions, the price source and its period, that CloudFix history was not queried, and that stopped rows end their `checks` early. Do not copy any of them onto rows, and do not restate a failed check's reason as a row qualification.
- Keep complete responses, fee estimates and calculations in the evidence files and point to them with `evidence_refs`. Do not paste them into a row.

## Totals

`totals.decision_counts` counts all rows by decision. Derive `totals.excluded_ids` from the findings: exactly the resource IDs with more than one recommended row, such as a bucket's separate billing rows. Keep every such row with its amounts and explain each excluded bucket in `reason_not_totaled`. These rows cannot safely be combined; no ordinary row may be excluded, including a negative saving. Omit `excluded_ids` or use `[]` when there are none.

Total every remaining recommended row's unrounded savings when all have known savings and share `currency`, `basis` and `estimation_method`. Include negative values. Add `current_annual` and `target_annual` over that same scope when every included row has the amount; otherwise keep that amount unknown. A partial run or any excluded bucket makes this a subtotal, not a complete savings headline.

Set `annual_savings` to null, with `reason_not_totaled`, when the remaining rows have unknown savings or incompatible currencies, price sources or methods, or when no rows remain. Do not choose the most favorable compatible group. Leave out the current and target totals too. With no recommended rows the total is null, not zero. When the remaining rows can be totaled, report that subtotal; keep the excluded buckets' separate billing rows visible after it.

The format has no first-year total. Where the report shows one, sum `first_year_savings` over exactly the same included rows as the annual savings total or subtotal, only when every included value is known. Do not add excluded billing rows into the first-year figure. When the annual total is null, do not report a first-year total either.

## Customer report

Lead with the result, written as the customer will see it. This is `report.md` of the filled example above, which `scripts/assess.py` prints; the guided path writes the same layout:

> **Estimated savings: $801.12/year across two buckets.**
>
> Account 000000000000 · us-east-1 · every bucket in the regional listing · 4 discovered, 4 assessed, 0 unassessed · complete · observed 2026-11-01T00:00:00.000Z · self-check, no independent review.
> First-year savings after one-time transition charges: $777.12.
>
> AWS public prices; estimate for the proposed configuration. Currency: USD.
>
> | Resource | Region | Proposed change | Current per year | After per year | Savings per year |
> | --- | --- | --- | ---: | ---: | ---: |
> | example-logs | us-east-1 | Lifecycle rule `CloudFix-Standard-To-Sit`: to Intelligent-Tiering at day 0 | $1080.00 | $546.00 | $534.00 |
> | example-media | us-east-1 | Lifecycle rule `CloudFix-Standard-To-Sit`: to Intelligent-Tiering at day 0 | $480.00 | $212.88 | $267.12 |
> | **Total: 2 buckets** | | | **$1560.00** | **$758.88** | **$801.12** |
>
> | Resource | Region | Outcome | Reason / what is needed |
> | --- | --- | --- | --- |
> | example-small | us-east-1 | Not recommended | Annual savings after object fees ($26.41) are not above the 50 USD minimum. |
> | example-tiered | us-east-1 | Not recommended | An enabled bucket-wide lifecycle rule (move-to-it) already transitions to Intelligent-Tiering. |
>
> - Model-based estimate. The future Intelligent-Tiering tier mix is modeled, not measured: storage is assumed to cost 0.4185 of the Standard price after the transition, with Standard-IA weighted at 0.65 of Standard. No object access pattern was measured.
> - AWS public prices for Standard and Standard-IA payload storage only, quoted for each bucket alone at its current size. Current cost is that modeled storage cost, not the bucket's bill. Discounts, requests, retrievals, early deletes, minimum-size and minimum-duration charges and other storage classes are excluded; no existing-monitoring credit and no retrieval or early-delete saving is assumed.
> - Savings are after the lower of two bounds: the monitoring fee (0.0025 USD per 1,000 objects per month) or the loss on objects below 128 KiB, estimated from the average object size. Objects below 128 KiB do not produce the modeled saving.
> - First-year savings also subtract a one-time transition charge (0.01 USD per 1,000 objects) for existing objects only; objects that arrive later incur further charges.
> - The object count is the latest daily value within 90 days and sizes are the first value returned within 3 days; they need not describe one instant.
> - CloudFix recommendation history was not queried, so RecommendationFinalized is not evaluated. Buckets were discovered with ListBuckets, not by CloudFix billing discovery.
> - Optional archive tiers, application retrieval needs and prefix-, tag- or noncurrent-version lifecycle rules were not assessed as safe. Nothing was changed.
> - A row that stopped early lists the finder's checks up to the one that stopped it; the later checks were not run.
> - example-media: 1 enabled lifecycle rule outside the bucket-wide checks (tmp-cleanup: prefix tmp/, expires after 7 days). Not tested as safe; review before any change.
>
> Nothing was executed: no lifecycle rule was created, no tag was written and no bucket was changed. These are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`.
>
> For more AWS optimization options, see [CloudFix](https://cloudfix.com/).

The headline names the total or subtotal and the number of included buckets; on a partial run it is a subtotal for the assessed scope. The second line gives the first-year savings after one-time transition charges over the same included rows, and is left out when the total is null. With excluded buckets the headline reads "Estimated subtotal: $X/year across N buckets; M buckets shown separately", the first table holds only the included buckets, and the excluded buckets' billing rows follow the total as `<bucket>#<n>` lines with their own amounts, under one line that gives `reason_not_totaled`. Without a total, the headline says how many opportunities were found, or "Savings not established." In the second table, more than three buckets that share one outcome and reason are grouped on one line with their count and the first three names.

The context line gives account, region, requested scope, buckets discovered, assessed and unassessed, run status, observation time and verification method. Unknown counts stay unknown. Never call a selection an account-wide assessment.

The price caption says "AWS public prices" for `public_price`, "account and region effective billing rate" for `account_effective_rate` with its billing period, "billed costs" for `resource_billed` and "supplied prices" for `caller_supplied`; the qualifications state that the figure is a model-based estimate, with its assumptions, and in `billing` that the rate was applied to each bucket's 7-day mean size, not each bucket's bill. After a conditional request that fell back, the first qualification says that billed costs were requested first and why public prices are shown.

The first table has one row per included recommended finding, largest savings first, then the total or subtotal. Round only for display. Use "—" for unknown and `$0` only for an established zero. Above 25 rows, show the 25 largest, state how many more are in `assessment.json`, and keep the total over all included rows. When no total can be established, the total line shows "Not established".

Every other finding appears once in the second table, as "Not recommended", "More evidence needed" or "Assessment error", with the failed check in plain words and its value, or the exact next read or input.

Close with the run-wide qualifications, any run-level failures, each bucket's own notes (here: `example-media` has a prefix-scoped expiration rule outside the bucket-wide checks), that nothing was executed, and the links to `assessment.json` and the evidence folder.

A completed empty listing says "nothing to optimize"; a failed listing does not. Charts are optional and use the same rows.

Every estimate also carries these limits. Sub-128-KiB objects do not produce the modeled tiering saving. Optional asynchronous archive tiers have different retrieval behavior and may already be enabled or unchecked. The native report's broad claims of access-pattern analysis and no performance impact are not evidence that those conditions were verified.

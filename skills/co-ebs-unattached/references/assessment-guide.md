# Assessment guide: unattached EBS volumes

Read this in stage 1 of `guided.md`, before the first read, and keep it open through stage 6; `scripts/assess.py` writes the same result. It fixes this skill's literal values, the meaning of each field of `assessment.json`, how checks become decisions and costs, the totals rule and the customer report. The schema is `assessment-v1.schema.json` beside this file. A file that passes the schema is well formed; that does not prove the AWS facts or the decisions are right.

An omitted or null value means unknown. Never write `0` for unknown.

## Contents

- Fixed values
- Field meanings
- Decisions
- Filled example
- Totals
- Customer report

## Fixed values

Use these literal values. Do not invent other names.

| Value | This skill |
| --- | --- |
| `schema_version` | `"1.0"` |
| `skill.id` | `co-ebs-unattached` |
| `skill.revision` | The `metadata.version` in the `SKILL.md` frontmatter, as a string |
| `run.mode` | `local_code` when `scripts/assess.py` produced the result, `guided` when the harness followed `guided.md` |
| `resource.type` | `AWS::EC2::Volume` |
| `recommendation.action` | `snapshot_then_delete_volume` |
| `recommendation.parameters` keys | `immediate_archival` (boolean; the finder's `immediateArchival`) |
| Check names, in order | `RecommendationFinalized`, `ResourceNotDeleted`, `IsValidState`, `VolumeExpectedType`, `VolumeAgeXDays`, `DetachedXDaysAgo`, `MinimalSavings`, `MaximumSavings` |
| `details` keys | `volume_type`, `size_gib`, `archive_rate`, `archive_rate_source`, `archive_annual`, `snapshots`, `framework_context_complete`; in `public_prices` mode also `public_price_model` on a row that was not calculated; in a public-price comparison also `public_price_model` and `regional_estimate`; with Cost Explorer costs also `effective_rates` and `cost_components` |
| `execution_status` | `not_executed` |

Each pricing mode fixes the cost fields of a calculated row:

| Requested mode | `scope.pricing_mode` | `costs.basis` | `costs.estimation_method` | Amounts come from |
| --- | --- | --- | --- | --- |
| `supplied-native-resource` | `supplied` | `caller_supplied` | `model_estimate` | The savings formula in `costing.md` on the supplied annual cost |
| `cost-explorer` | `billing` | `account_effective_rate` | `model_estimate` | The same formula on the annual cost built in `billing-cost-explorer.md`; `costs.source_period` is the 31-day Cost Explorer window |
| `cur` | `billing` | `resource_billed` | `model_estimate` | The same formula on the CUR annual cost; `costs.source_period` is the 31-day CUR window |
| `public-price-model` | `public_prices` | `public_price` | `model_estimate` | The two calculators in `public-price-model.md` |
| `technical-only` | `technical_only` | omitted | omitted | Nothing: `costs` is `{"status": "not_calculated"}` |

The method is always `model_estimate`, because the after cost assumes an archived snapshot is billed for the full volume size for twelve months. State that assumption once in the top-level `qualifications`.

An `account_effective_rate` row always has `costs.source_period` and the top-level qualification worded in `billing-cost-explorer.md`. A `resource_billed` row comes only from the CUR recipe. `billing-cost-explorer.md` has a filled `costs` object for a Cost Explorer row with its arithmetic.

## Field meanings

| Field | Meaning for this skill |
| --- | --- |
| `skill.source_ref` | The commit the skill was fetched at, or `sha256:` plus the hash of its `SKILL.md` (`run_metadata.skill_revision`), or null. Never invent it. |
| `run.observed_at` | `asOf`, in UTC. |
| `run.status` | `complete`: every requested volume has a final decision, including rejections and a completed empty inventory. `partial`: at least one requested volume is unresolved. `failed`: an error prevented the assessment, for example identity or the whole inventory failed. |
| `run_metadata` | Top-level copy of the object created in stage 1, with the configured `volumeAgeThreshold` and `minimalSavings`. |
| `scope` | `account_id` is the account from `GetCallerIdentity`, null only when identity could not be established. `resource_ids` is the explicit selection, or null when every `available` volume in the regions was discovered. `pricing_mode` is what the customer asked for; after the conditional fallback described under Decisions it is `public_prices`. |
| `coverage` | Counts distinct volumes, not rows. `discovered_count` is the selection size or the inventory size. `assessed_count` counts volumes decided `recommended` or `not_recommended`; `unassessed_ids` lists the rest. `inventory_complete` is true only when every `DescribeVolumes` page was read. A selected ID that no volume read in a requested region returns is still one discovered, assessed volume: rule 2 rejects it. Unknown counts are null. |
| `findings[].id` | The volume ID. One volume has one finding. |
| `decision` | `recommended`, `not_recommended`, `needs_evidence` or `error`; see the next section. |
| `technical_eligibility` | Optional. Omit it on `recommended` rows. Add `eligible` on any other row for which rules 2–6 all passed, such as a savings rejection or a price that could not be resolved. Leave it out otherwise. |
| `checks` | All eight checks in order, each `pass`, `fail` or `not_evaluated`, as `checks.md` describes. Anything but `pass` carries a `reason`; `value` is the check's value object. |
| `recommendation` | `action` and `parameters`, on recommended rows only. Leave it out or null on every other row. |
| `costs` | `status` is `calculated` only when the amounts were computed and rules 7 and 8 were evaluated on them; then `basis`, `estimation_method`, `currency`, `current_annual`, `target_annual` and `annual_savings` are all present and unrounded. Otherwise `not_calculated`; a known current cost may still be kept with its `basis` and `currency`. A current cost is known when it needs no further read: a supplied cost naming the volume, the established Cost Explorer rates for its type, or the resolved public prices for its type and size. Carry it on every row that has one, including a row rejected by an earlier check; a row with no known current cost carries `{"status": "not_calculated"}` only. `one_time_cost` and `first_year_savings` stay out unless established. |
| `details` | Volume type and size; the archive rate, its source (`native_lookup`, `fallback`, `regional_quote` or `supplied`) and the computed archive cost; the snapshot list (`null` when the snapshot read failed or is incomplete); `framework_context_complete:false` when CloudFix history was not available. Keep further finder facts and caller metadata here. Never drop a fact to fit the format. |
| `qualifications` | Run-wide limits, stated once at the top level. Put only row-specific limits, such as the fallback archive rate, in that row's `costs.qualifications`. |
| `evidence_refs` | `path#/json/pointer`, relative to the folder holding `assessment.json`. Required on recommended rows. Use the top-level list for run-wide reads such as identity, inventory and prices. |
| `issues` | What is missing, failed or worth flagging. Every `needs_evidence` or `error` row has one, with `request` naming the exact next read or input. One failure that affects many volumes is one issue without a `resource_id`: a failed, incomplete or missing `GetMetricData` call is one such issue carrying that call's exact request, and each of its rows has an issue of its own that names the call and does not repeat the request. |
| `verification` | `method` is `self_check`, `independent_review` or `none`; `summary` says what was and was not checked. Never record a review that did not happen. |
| `source_output` | Optional, only when `native-projection.md` was requested. |

## Decisions

Apply in order:

1. A completed rejecting check gives `not_recommended`, even if other reads are missing or failed. Keep its reason and any known costs. A demonstrated rejection stays visible even when other evidence is missing.
2. Otherwise a required read or calculation that failed gives `error`, and a required input that is absent gives `needs_evidence`.
3. Only complete, passing checks for the requested scope and pricing mode give `recommended`. A priced request needs calculated savings; `technical_only` does not. CloudFix recommendation history that a standalone run cannot see leaves rule 1 `not_evaluated` with a stated qualification; it is never a fabricated pass and does not block the recommendation.

The rule text uses this skill's older labels. `eligible` is `recommended`, `rejected` is `not_recommended`, `needs_evidence` stays `needs_evidence`, and `read_error` is `error`. Where `checks.md` marks the harness run `read_error`, the affected rows are `error` and `run.status` follows the field table. Map by the facts above, not by the label alone.

| Situation | `decision` | What the row shows |
| --- | --- | --- |
| Rules 2–8 pass; rule 1 passes, or is `not_evaluated` because CloudFix history is unavailable | `recommended` | `recommendation`, calculated `costs`, `evidence_refs` |
| `technical_only` and rules 2–6 pass | `recommended` | Rules 7 and 8 `not_evaluated`; `costs` not calculated; no total |
| Rule 2, 3, 4 or 6 fails on observed evidence | `not_recommended` | The failing rule `fail`; later rules `not_evaluated`; `costs` not calculated |
| Rule 1 or 5 fails | `not_recommended` | The failing rule `fail`; later rules as actually evaluated |
| Rule 7 or 8 fails | `not_recommended` | `technical_eligibility:"eligible"` when rules 2–6 all passed; the calculated `costs` that were compared |
| A read failed: `GetMetricData` (rule 6 `detectionMethod:"error"`), a denied `DescribeVolumes` batch, a denied or failed Cost Explorer call, the CUR query, or a price read in `public_prices` mode; or a per-item exception or failed calculation | `error` | An issue of kind `read_error` or `calculation_error` naming the read to repeat |
| An input is absent: a read not performed or missing pages; in a priced mode a cost that is missing or not in USD; in `billing` mode a needed Cost Explorer usage type without usable usage or a verifiable unit, or with zero cost over positive usage; in `public_prices` mode an unknown magnetic I/O quantity, an unsupported placement or a missing or ambiguous quote | `needs_evidence` | An issue of kind `missing_evidence` naming the read or input |

In `supplied` and `billing` mode a failed archive price lookup is not an error: the finder's fallback rate applies and the row says so. A failed snapshot read never changes a decision; record it as an issue of kind `read_error` and set `details.snapshots` to null. Missing cost prevents a complete financial assessment in the requested basis; a supported public estimate is separately scoped and labelled, never native financial approval.

The only fallback from billed costs to public prices is the conditional request of `billing-cost-explorer.md`.

## Filled example

One complete run in `public_prices` mode, as `scripts/assess.py` writes it: two recommended volumes and one rejected volume. Identities and prices are illustrative; do not reuse the numbers or treat them as AWS quotes.

```json
{
  "schema_version": "1.0",
  "skill": {
    "id": "co-ebs-unattached",
    "revision": "0.4.0",
    "source_ref": null
  },
  "run": {
    "observed_at": "2026-11-01T00:00:00Z",
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
    "discovered_count": 3,
    "assessed_count": 3,
    "unassessed_ids": []
  },
  "run_metadata": {
    "finderId": "UnusedEBSVolumes",
    "skill_revision": null,
    "accountId": "000000000000",
    "region": "us-east-1",
    "asOf": "2026-11-01T00:00:00Z",
    "evaluation_timezone": "UTC",
    "configuration": {
      "volumeAgeThreshold": 7,
      "minimalSavings": 0
    },
    "configuration_overrides": {},
    "run_classification": "standard",
    "experiment": null,
    "public_price_fallback_allowed": false
  },
  "findings": [
    {
      "id": "vol-example-a",
      "resource": {
        "id": "vol-example-a",
        "type": "AWS::EC2::Volume",
        "region": "us-east-1"
      },
      "decision": "recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history is not available to a standalone run."
        },
        {
          "name": "ResourceNotDeleted",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-a"
          }
        },
        {
          "name": "IsValidState",
          "status": "pass",
          "value": {
            "volumeState": "available",
            "requiredState": "available"
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "gp3",
            "expectedTypes": [
              "gp2",
              "gp3",
              "io1",
              "io2",
              "st1",
              "sc1",
              "standard"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "pass",
          "value": {
            "createTime": "2026-03-01T00:00:00.000Z",
            "ageDays": 245,
            "minAgeDays": 30
          }
        },
        {
          "name": "DetachedXDaysAgo",
          "status": "pass",
          "value": {
            "detectionMethod": "cloudwatch",
            "lastAttachmentDate": "2026-08-15T00:00:00.000Z",
            "maxLastAttachmentDate": "2026-10-25T00:00:00.000Z"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 81.0,
            "annualCost": 96.0,
            "minimalSavings": 0
          },
          "reason": "model_checks: public-price estimate, 81 > 0."
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 81.0,
            "annualCost": 96.0
          },
          "reason": "model_checks: public-price estimate, 81 <= 96."
        }
      ],
      "recommendation": {
        "action": "snapshot_then_delete_volume",
        "parameters": {
          "immediate_archival": false
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "model_estimate",
        "currency": "USD",
        "current_annual": 96.0,
        "target_annual": 15.0,
        "annual_savings": 81.0
      },
      "details": {
        "volume_type": "gp3",
        "size_gib": 100,
        "framework_context_complete": false,
        "archive_rate": 0.0125,
        "archive_rate_source": "regional_quote",
        "archive_annual": 15.0,
        "snapshots": []
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/0/output/Volumes/0",
        "evidence/get-metric-data.json#/0/output/MetricDataResults",
        "evidence/pricing.json#/0",
        "evidence/pricing.json#/2",
        "evidence/calculations.json#/vol-example-a"
      ]
    },
    {
      "id": "vol-example-b",
      "resource": {
        "id": "vol-example-b",
        "type": "AWS::EC2::Volume",
        "region": "us-east-1"
      },
      "decision": "recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history is not available to a standalone run."
        },
        {
          "name": "ResourceNotDeleted",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-b"
          }
        },
        {
          "name": "IsValidState",
          "status": "pass",
          "value": {
            "volumeState": "available",
            "requiredState": "available"
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "st1",
            "expectedTypes": [
              "gp2",
              "gp3",
              "io1",
              "io2",
              "st1",
              "sc1",
              "standard"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "pass",
          "value": {
            "createTime": "2025-01-15T08:30:00.000Z",
            "ageDays": 654,
            "minAgeDays": 30
          }
        },
        {
          "name": "DetachedXDaysAgo",
          "status": "pass",
          "value": {
            "detectionMethod": "no_data",
            "maxLastAttachmentDate": "2026-10-25T00:00:00.000Z"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 195.0,
            "annualCost": 270.0,
            "minimalSavings": 0
          },
          "reason": "model_checks: public-price estimate, 195 > 0."
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 195.0,
            "annualCost": 270.0
          },
          "reason": "model_checks: public-price estimate, 195 <= 270."
        }
      ],
      "recommendation": {
        "action": "snapshot_then_delete_volume",
        "parameters": {
          "immediate_archival": true
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "model_estimate",
        "currency": "USD",
        "current_annual": 270.0,
        "target_annual": 75.0,
        "annual_savings": 195.0
      },
      "details": {
        "volume_type": "st1",
        "size_gib": 500,
        "framework_context_complete": false,
        "archive_rate": 0.0125,
        "archive_rate_source": "regional_quote",
        "archive_annual": 75.0,
        "snapshots": []
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/0/output/Volumes/1",
        "evidence/get-metric-data.json#/0/output/MetricDataResults",
        "evidence/pricing.json#/1",
        "evidence/pricing.json#/2",
        "evidence/calculations.json#/vol-example-b"
      ]
    },
    {
      "id": "vol-example-c",
      "resource": {
        "id": "vol-example-c",
        "type": "AWS::EC2::Volume",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "CloudFix recommendation history is not available to a standalone run."
        },
        {
          "name": "ResourceNotDeleted",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-c"
          }
        },
        {
          "name": "IsValidState",
          "status": "pass",
          "value": {
            "volumeState": "available",
            "requiredState": "available"
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "expectedTypes": [
              "gp2",
              "gp3",
              "io1",
              "io2",
              "st1",
              "sc1",
              "standard"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "pass",
          "value": {
            "createTime": "2026-06-01T12:00:00.000Z",
            "ageDays": 152,
            "minAgeDays": 30
          }
        },
        {
          "name": "DetachedXDaysAgo",
          "status": "fail",
          "value": {
            "detectionMethod": "cloudwatch",
            "lastAttachmentDate": "2026-10-30T00:00:00.000Z",
            "maxLastAttachmentDate": "2026-10-25T00:00:00.000Z"
          },
          "reason": "Last attachment evidence on 2026-10-30 is not before the 2026-10-25 cutoff (7 days)."
        },
        {
          "name": "MinimalSavings",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
        },
        {
          "name": "MaximumSavings",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
        }
      ],
      "costs": {
        "status": "not_calculated",
        "basis": "public_price",
        "currency": "USD",
        "current_annual": 60.0
      },
      "details": {
        "volume_type": "gp2",
        "size_gib": 50,
        "framework_context_complete": false
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/0/output/Volumes/2",
        "evidence/get-metric-data.json#/0/output/MetricDataResults",
        "evidence/calculations.json#/vol-example-c"
      ]
    }
  ],
  "totals": {
    "decision_counts": {
      "recommended": 2,
      "not_recommended": 1,
      "needs_evidence": 0,
      "error": 0
    },
    "annual_savings": 276.0,
    "currency": "USD",
    "basis": "public_price",
    "estimation_method": "model_estimate",
    "current_annual": 366.0,
    "target_annual": 90.0
  },
  "issues": [],
  "qualifications": [
    "Model-based estimate: the after cost prices an archived snapshot at the full volume size for twelve months at the regional archive rate. Volume size does not establish what a snapshot is billed for.",
    "Not priced: existing snapshots, standard-tier residence before archiving, the 90-day archive minimum, retrieval and early-deletion charges, discounts and taxes. One-time costs and first-year savings are unknown.",
    "Current cost is a twelve-month AWS public-price run rate for the current settings, not billed cost.",
    "CloudFix recommendation history was not available, so RecommendationFinalized was not evaluated for any volume. The total covers 2 volumes and 276 USD assessed without that history.",
    "Volumes were discovered by status=available; parity with CloudFix billing-based discovery was not checked.",
    "An available state and missing attachment metrics do not establish owner intent or recovery readiness.",
    "Detachment threshold: 7 days (default); creation age minimum 30 days; dates evaluated in UTC."
  ],
  "verification": {
    "method": "self_check",
    "summary": "Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. Each volume's calculator inputs and results are in evidence/calculations.json; its check values are in assessment.json. AWS facts were not re-read. No independent review took place."
  },
  "evidence_refs": [
    "evidence/run-metadata.json",
    "evidence/get-caller-identity.json#/0",
    "evidence/describe-volumes.json",
    "evidence/describe-snapshots.json",
    "evidence/pricing.json"
  ],
  "execution_status": "not_executed"
}
```

A row that could not be resolved, and the run-level issue that says why and what to read next (the same volume when `GetMetricData` answers `Forbidden`; the row's own issue names this call):

```json
{
  "finding": {
    "id": "vol-example-a",
    "resource": {
      "id": "vol-example-a",
      "type": "AWS::EC2::Volume",
      "region": "us-east-1"
    },
    "decision": "error",
    "checks": [
      {
        "name": "RecommendationFinalized",
        "status": "not_evaluated",
        "reason": "CloudFix recommendation history is not available to a standalone run."
      },
      {
        "name": "ResourceNotDeleted",
        "status": "pass",
        "value": {
          "resourceId": "vol-example-a"
        }
      },
      {
        "name": "IsValidState",
        "status": "pass",
        "value": {
          "volumeState": "available",
          "requiredState": "available"
        }
      },
      {
        "name": "VolumeExpectedType",
        "status": "pass",
        "value": {
          "volumeType": "gp3",
          "expectedTypes": [
            "gp2",
            "gp3",
            "io1",
            "io2",
            "st1",
            "sc1",
            "standard"
          ]
        }
      },
      {
        "name": "VolumeAgeXDays",
        "status": "pass",
        "value": {
          "createTime": "2026-03-01T00:00:00.000Z",
          "ageDays": 245,
          "minAgeDays": 30
        }
      },
      {
        "name": "DetachedXDaysAgo",
        "status": "not_evaluated",
        "reason": "GetMetricData returned Forbidden for query readOps_vol_example_a. Attachment history is unknown. The finder itself records this as a failed row.",
        "value": {
          "detectionMethod": "error",
          "maxLastAttachmentDate": "2026-10-25T00:00:00.000Z"
        }
      },
      {
        "name": "MinimalSavings",
        "status": "not_evaluated",
        "reason": "Not run: a required read failed."
      },
      {
        "name": "MaximumSavings",
        "status": "not_evaluated",
        "reason": "Not run: a required read failed."
      }
    ],
    "costs": {
      "status": "not_calculated",
      "basis": "public_price",
      "currency": "USD",
      "current_annual": 96.0
    },
    "details": {
      "volume_type": "gp3",
      "size_gib": 100,
      "framework_context_complete": false
    },
    "evidence_refs": [
      "evidence/describe-volumes.json#/0/output/Volumes/0",
      "evidence/get-metric-data.json#/0/output/MetricDataResults",
      "evidence/calculations.json#/vol-example-a"
    ]
  },
  "issue": {
    "kind": "read_error",
    "stage": "collection",
    "message": "GetMetricData call 1 (3 volumes, vol-example-a to vol-example-c): GetMetricData returned Forbidden for query readOps_vol_example_a. This issue's request is the exact read to repeat.",
    "request": {
      "kind": "aws_read",
      "name": "GetMetricData",
      "service": "cloudwatch",
      "region": "us-east-1",
      "parameters": {
        "MetricDataQueries": [
          {"Id": "readOps_vol_example_a", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeReadOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-a"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "writeOps_vol_example_a", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeWriteOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-a"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "queueLength_vol_example_a", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeQueueLength", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-a"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "readOps_vol_example_b", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeReadOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-b"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "writeOps_vol_example_b", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeWriteOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-b"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "queueLength_vol_example_b", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeQueueLength", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-b"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "readOps_vol_example_c", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeReadOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-c"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "writeOps_vol_example_c", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeWriteOps", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-c"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true},
          {"Id": "queueLength_vol_example_c", "MetricStat": {"Metric": {"Namespace": "AWS/EBS", "MetricName": "VolumeQueueLength", "Dimensions": [{"Name": "VolumeId", "Value": "vol-example-c"}]}, "Period": 86400, "Stat": "Sum"}, "ReturnData": true}
        ],
        "StartTime": "2025-11-01T00:00:00.000Z",
        "EndTime": "2026-11-01T23:59:59.999Z",
        "ScanBy": "TimestampDescending"
      }
    },
    "evidence_refs": [
      "evidence/get-metric-data.json#/0/output/MetricDataResults"
    ]
  }
}
```

Such a run is `partial`. A fault on one query fails every volume of that call, as the finder does, so all three volumes are in `coverage.unassessed_ids` and under `decision_counts.error`, and `annual_savings` is null.

## Totals

`totals.decision_counts` counts rows by decision. `totals.annual_savings` is the one headline number: the sum of every recommended row's unrounded savings, with their shared `currency`, `basis` and `estimation_method`. Add `current_annual` and `target_annual` the same way when every recommended row has them. Rows that are not recommended never contribute, whatever amounts they keep. On a partial run the total is a subtotal for the volumes assessed so far.

Set `annual_savings` to null and give `reason_not_totaled` when the rows cannot honestly be added: savings were not estimated (`technical_only`), or the rows differ in currency, price source or method. Then leave out the current and target totals too. With no recommended rows the total is null, not zero. Unknown/zero are distinct.

When CloudFix history was not available for some or all recommended rows, the total is a standalone estimate. Say so in `qualifications` with the number of such rows and their summed savings. Never present such a figure as native-complete totals.

## Customer report

The report is `report.md`, printed by the script and written beside `assessment.json`. For the example above it reads:

> **Estimated savings: $276.00/year across two volumes.**
>
> Account 000000000000 · us-east-1 · all available volumes · 3 discovered, 3 assessed, 0 unassessed · complete · observed 2026-11-01T00:00:00Z · self-check.
>
> AWS public prices; estimate for the proposed configuration. Currency: USD.
>
> | Resource | Region | Proposed change | Current per year | After per year | Savings per year |
> | --- | --- | --- | ---: | ---: | ---: |
> | vol-example-b | us-east-1 | Snapshot, then delete st1, 500 GiB; immediate archival: yes | $270.00 | $75.00 | $195.00 |
> | vol-example-a | us-east-1 | Snapshot, then delete gp3, 100 GiB; immediate archival: no | $96.00 | $15.00 | $81.00 |
> | **Total: 2 volumes** | | | **$366.00** | **$90.00** | **$276.00** |
>
> | Resource | Region | Outcome | Reason / what is needed |
> | --- | --- | --- | --- |
> | vol-example-c | us-east-1 | Not recommended | Last attachment evidence on 2026-10-30 is not before the 2026-10-25 cutoff (7 days). |
>
> - Model-based estimate: the after cost prices an archived snapshot at the full volume size for twelve months at the regional archive rate. Volume size does not establish what a snapshot is billed for.
> - Not priced: existing snapshots, standard-tier residence before archiving, the 90-day archive minimum, retrieval and early-deletion charges, discounts and taxes. One-time costs and first-year savings are unknown.
> - Current cost is a twelve-month AWS public-price run rate for the current settings, not billed cost.
> - CloudFix recommendation history was not available, so RecommendationFinalized was not evaluated for any volume. The total covers 2 volumes and 276 USD assessed without that history.
> - Volumes were discovered by status=available; parity with CloudFix billing-based discovery was not checked.
> - An available state and missing attachment metrics do not establish owner intent or recovery readiness.
> - Detachment threshold: 7 days (default); creation age minimum 30 days; dates evaluated in UTC.
> - vol-example-b: st1, available, 654 days old. No attachment metrics in the 365-day lookback, which the finder reads as detached before 2025-11-01. No snapshots found.
> - vol-example-a: gp3, available, 245 days old. Last attachment evidence 2026-08-15, before the 2026-10-25 cutoff. No snapshots found.
>
> Nothing was executed; these are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`.
>
> For more AWS optimization options, see [CloudFix](https://cloudfix.com/).

A report made with `--replay` starts with one more line, saying that it was replayed from saved evidence and when that evidence was observed.

The headline comes first: the sum of the recommended rows, or a subtotal for the assessed scope on a partial run; without a total, how many opportunities were found, or “Savings not established.” A completed empty inventory says “nothing to optimize”; a failed inventory does not.

The context line gives the account, region, requested scope (a selection is never called an account-wide assessment), volumes discovered, assessed and unassessed, run status, observation time and verification method; unknown counts stay unknown. The price caption names the basis: “AWS public prices” for `public_price`, “Billed costs” for `resource_billed`, “Account and region effective billing rate” with its period for `account_effective_rate`, “Supplied prices” for `caller_supplied`.

The first table holds one row per recommended finding, largest savings first, then the total; amounts are rounded only for display. Above 25 rows the 25 largest are shown with a count of the rest. The second table holds every other finding with “Not recommended”, “More evidence needed” or “Assessment error” and the reason or the exact next read.

Then the qualifications, including the configured detachment threshold and any experimental overrides, run-level failures, and one line per recommended volume: type, state, creation age, the attachment date from metrics or the finder's no-data inference (detached before the lookback, not proof of inactivity), and the snapshot context (“unknown” after a failed read, never “none exist”). Keep this faithful to the facts; do not reproduce an unsafe native prose assertion as verified truth. Report current cost/estimated savings/new cost only when justified. The report closes with the statement that nothing was executed, the links to `assessment.json` and the evidence folder, and “For more AWS optimization options, see [CloudFix](https://cloudfix.com/).”

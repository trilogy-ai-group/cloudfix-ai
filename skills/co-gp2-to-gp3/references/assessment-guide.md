# Assessment guide: gp2 to gp3

Read this at stage 1 of `guided.md`: it fixes the shape of the result, for the script and for the guided path alike. `guided.md` stage 2 names the evidence files; `scripts/assess.py` writes the same files. `assessment.json` must be valid against `assessment-v1.schema.json` in this folder. This guide fixes the literal values for this skill, explains the fields, shows one filled example and gives the report layout. It changes no check, formula or decision.

An omitted or null value means unknown. Never write `0` for unknown.

## Contents

- Fixed values for this skill
- Checks
- Decisions
- Field meanings
- Filled example
- Totals
- Customer report

## Fixed values for this skill

Use these literal values. Do not invent other names.

| Value | Literal |
| --- | --- |
| `schema_version` | `"1.0"` |
| `skill.id` | `co-gp2-to-gp3` |
| `skill.revision` | The `metadata.version` in the `SKILL.md` frontmatter |
| `run.mode` | `local_code` when `scripts/assess.py` produced the result, `guided` when the harness followed `guided.md` |
| `resource.type` | `AWS::EC2::Volume` |
| `recommendation.action` | `change_volume_type` |
| `recommendation.parameters` keys | `volume_type` (always `gp3`), `size_gib` (the unchanged `Size`), `iops` (the finder's `gp3Iops`), `throughput_mibps` (the finder's `gp3Throughput`, MiB/s) |
| Check names, in order | `ResourceExists`, `ResourceFixed`, `VolumeExpectedType`, `VolumeAgeXDays`, `EMRClusterAssociation` or `Ec2InstanceDescribable`, `ElasticVolumeEnabled`, `MinimalSavings`, `MaximumSavings` |
| `scope.pricing_mode` supported | `public_prices`, `billing`, `supplied` |
| `costs.currency` | `USD` |
| `execution_status` | `not_executed` |

Pricing mode decides the cost labels. Every mode uses the same finder arithmetic: `current_annual` is the finder's `currentAnnualCost`, `target_annual` its `gp3TargetCost` and `annual_savings` its `annualSavings`, all unrounded.

| `scope.pricing_mode` | Current cost comes from | `costs.basis` | `costs.estimation_method` |
| --- | --- | --- | --- |
| `public_prices` | Public gp2 price for the volume's size, used as a disclosed illustration of the current cost | `public_price` | `configuration_projection` |
| `billing` | Cost Explorer account-and-region effective gp2 rate applied to the volume's size; set `costs.source_period` to the billing window, end exclusive | `account_effective_rate` | `configuration_projection` |
| `supplied` | Annual costs supplied by the user, unchanged | `caller_supplied` | `configuration_projection` |

In `billing` mode `costs.source_period` is `{"start": "<Start>T00:00:00Z", "end": "<End>T00:00:00Z"}`: the Cost Explorer `Start` and `End` dates as UTC date-times, end exclusive.

In `billing` and `supplied` modes the target cost applies the public gp3-to-gp2 price ratio to the current cost; state that as a qualification. The finder calculates no one-time cost and no first-year saving: leave `one_time_cost` and `first_year_savings` out.

**Billed costs never become public prices silently.** A billed-cost request stays `billing` when Cost Explorer fails; the affected volumes follow the billing outcomes under Special outcomes, and no public-price amount appears in `costs`, `totals` or the report headline. The one exception is a conditional request in the customer's own words, such as "use billed costs if possible, otherwise public prices": try Cost Explorer; if it cannot produce the rate, say exactly why, then price the whole run at public prices. Save `scope.pricing_mode: "public_prices"` with `costs.basis: "public_price"`, one issue of kind `qualification` holding the Cost Explorer failure and its evidence reference, and a first qualification saying that billed costs were tried and why they failed. One run uses one price basis, so its total stays addable.

## Checks

Save the eight checks in the order above, one entry each. `name` is the finder's name. `value` is the finder's exact value shape from `checks.md`. `status` records what was actually evaluated:

- `pass` when the finder's check approved, `fail` when it rejected. A `fail` carries a `reason`.
- `not_evaluated`, with a `reason`, for a check skipped after a fail-fast rejection ("Not run after the earlier rejection.") and for a check that could not run because a read or input is missing or failed.
- The fifth entry is named `EMRClusterAssociation`, or `Ec2InstanceDescribable` when the finder emits that row in its place.
- When the finder ran the two savings checks without a calculated saving, they ran on its initialized zero savings: a placeholder. This happens after an age, EMR, instance describability or elastic rejection, and when a missing or zero `Size` skipped the calculation. Save both as `not_evaluated`, keep the finder's `value`, and say in the `reason` that no saving was calculated. An earlier failed check is the customer's reason. A missing or zero `Size` with no failed check gives `needs_evidence`, with a `missing_evidence` issue that requests a valid `Size` for that volume.
- `RecommendationFinalized`, the finder's first ledger row, is not saved as a check. A standalone run cannot see CloudFix recommendation history, so state it once as a run-wide qualification.
- A per-item exception (see `checks.md`) is not a check result. Save the check that threw as `not_evaluated` with the exception context and message as its `reason`, the later checks as `not_evaluated`, and the exact `Exception` row in the row's `details.exception`. Add an issue of kind `calculation_error`.

## Decisions

Apply in order. The finder's own outcome names are shown for reference only; save the common value.

1. A completed rejecting check gives `not_recommended` (finder: `do_not_proceed`), even if another read that still leaves rows (rule 2) is missing or failed. Keep its reason and any known costs.
2. Otherwise a Cost Explorer read that failed or was denied, or a calculation that failed, gives `error` (finder: `assessment_error`), and a required input or read that is absent gives `needs_evidence`. A failed or denied identity, price, volume or instance read writes no row: see *Run stopped early*.
3. Only complete, passing checks for the requested scope and pricing mode, with calculated savings, give `recommended` (finder: `proceed`).

A selected volume that the completed volume read does not return fails `ResourceExists` and is `not_recommended`. A supplied successful empty instance read produces the `Ec2InstanceDescribable` rejection; it is not missing evidence. Do not conceal a failed check while requesting another input.

Omit `technical_eligibility` on `recommended` rows. Set it to `eligible` only on a `not_recommended` volume that passed every resource check and was rejected by a savings check on calculated savings. Otherwise leave it out.

## Field meanings

| Field | Meaning for this skill |
| --- | --- |
| `skill.source_ref` | The commit this skill was fetched at, or `sha256:` plus the hash of its `SKILL.md`, or null. Never invent it. |
| `run.observed_at` | The declared assessment time used for the age check: the current time for a live run, the supplied fixed time for recorded evidence. |
| `run.status` | `complete`: every requested volume is `recommended` or `not_recommended`, or a completed inventory was empty. `partial`: at least one requested volume is unresolved or was not examined. `failed`: no volume could be assessed and no row is written. The table under *Run stopped early* gives the status for each read. |
| `run.evidence_root` | Folder that evidence references resolve from. Leave it out when `assessment.json` sits beside the `evidence/` folder. |
| `scope` | `account_id` is the verified workload account, null only when identity could not be established. `regions` holds the one assessed region. `resource_ids` is the explicit selection, or null when every gp2 volume in the region was requested. `pricing_mode` is the mode the results use: what the customer asked for, or `public_prices` when the customer allowed that fallback and Cost Explorer produced no rate. |
| `coverage` | Counts volumes, not rows. `discovered_count` is the number of selected IDs, or the gp2 volumes returned by a completed inventory. `assessed_count` counts volumes with a `recommended` or `not_recommended` decision. `unassessed_ids` lists every other volume in scope, including any not examined. `inventory_complete` is true only when every page of the scoped read completed. Unknown counts are null. |
| `findings[].id` | The volume ID. One row per volume. |
| `recommendation` | On `recommended` rows only. Leave it out everywhere else. |
| `costs.status` | `calculated` when `annual_savings` is an established number, including a zero or negative saving on a row rejected by a savings check. Otherwise `not_calculated`; a known current cost may still be kept with its `basis` and `currency`, such as the public baseline of a rejected volume in `public_prices` mode. In `billing` mode a public baseline stays in the evidence, never in `costs`. A current cost is known when it needs no further read: a supplied cost naming the volume, the established Cost Explorer rate, or the resolved public gp2 price applied to its size. Carry it on every row that has one, including a row rejected by an earlier check; a row with no known current cost carries `{"status": "not_calculated"}` only. |
| `costs.qualifications`, row `qualifications` | Only limits specific to that row, such as a price fallback or input substitution. Run-wide limits go once in the top-level `qualifications`. |
| `evidence_refs` | `path#/json/pointer` into the saved evidence files, relative to `run.evidence_root`. Required on `recommended` rows: at least the volume record and its calculation. Use the top-level list for run-wide reads such as identity, prices and inventory. |
| `issues` | What is missing, failed or worth flagging. Every `needs_evidence` row has a `missing_evidence` issue and every `error` row a `read_error` or `calculation_error` issue, with `request` naming the exact next read or input: `{kind: "aws_read", name, service, region, parameters}` with the exact request input, or `{kind: "input", name}`. `stage` is `scope`, `collection`, `analysis`, `costing`, `verification` or `report`. When one failure affects many volumes, one issue without `resource_id` covers them all. |
| `verification` | `method` is `self_check` for the stage 5 check, `independent_review` only when a separate reviewer actually examined the evidence, `none` when nothing was checked. `summary` says what was and was not checked. |
| `details` | Finder facts with no common field. On a row: `savings_calculated` (true or false), `finderReportData` (the finder's report fields under their own names, `{}` when parameters were never populated) and `exception` when one occurred. At the top level: `run_metadata` (the object defined in `scope-and-access.md`) and, when calculated separately, `public_price_totals`. |
| `source_output` | Optional. Only when a native-shaped projection was explicitly requested: `{kind: "guided_projection", evidence_ref}` pointing at the saved projection row. |

State these run-wide `qualifications` whenever they apply:

- CloudFix recommendation history was not queried; the finder's `RecommendationFinalized` row rests on an empty local-adapter assumption.
- Unchecked pipeline context: finalized recommendation history, tenant settings/discovery threshold unless supplied, CUR discovery parity, pipeline persistence/monitoring and change-request revalidation. A resource assessment is not a claim of full connected-pipeline equivalence.
- A live inventory replaces CloudFix's billing-data discovery; name the discovery conditions that were not checked.
- The price basis: a public-price illustration, supplied costs, or every Cost Explorer qualification listed in `billing.md`, including each fallback, missing-day count and `Estimated` flag.
- The finder does not test workload performance, utilization, instance state or the other conditions listed under limits in `checks.md`; workload safety has not been demonstrated.
- One-time change costs and first-year savings were not established.

Special outcomes:

- **Empty inventory.** A completed, fully paginated inventory with no gp2 volumes is `complete` with no findings, `discovered_count` and `assessed_count` 0 and a null total.
- **Billing unavailable.** No billed savings are calculated. A volume already rejected by a completed check stays `not_recommended`. Every other volume has `costs.status` `not_calculated` and is covered by one issue at stage `costing` whose `request` names the Cost Explorer read to repeat. When that read was denied, failed or returned invalid data, those volumes are `error` with a `read_error` issue. When it completed but returned no scoped GP2 usage type or no positive usable usage, they are `needs_evidence` with a `missing_evidence` issue. The run is `partial`. Keep the public baselines in the evidence, not as savings.
- **Run stopped early.** A `failed` run has unknown counts null and no invented rows. What each read gives when it is denied or fails, and when recorded evidence does not supply it:

  | Read | Denied or failed | Not supplied |
  | --- | --- | --- |
  | Identity | `failed`, no rows, `read_error` at `scope` | `failed`, no rows, `missing_evidence` at `scope` |
  | Price lookup | `failed`, no rows, `read_error` at `collection` | `failed`, no rows, `missing_evidence` at `collection` |
  | Volume inventory | `failed`, no rows, `read_error` at `collection` | `partial`, no rows, `missing_evidence` at `collection` |
  | Volume read by ID | `failed`, no rows, `read_error` at `collection` | `partial`; a returned volume that a completed check rejects stays `not_recommended` (rule 1), every other volume is `needs_evidence`, returned or not, until the missing page is read; `missing_evidence` at `collection` |
  | Instance read | `failed`, no rows, `read_error` at `collection` | `partial`, the volumes attached to the unread instances are `needs_evidence`, `missing_evidence` at `collection` |
  | Cost Explorer | `partial`, `error` rows, `read_error` at `costing` | `partial`, `needs_evidence` rows, `missing_evidence` at `costing` |

  In every `partial` case a volume that a completed check already rejected stays `not_recommended`.

## Filled example

Three gp2 volumes discovered in one region, `public_prices` mode, illustrative prices ($0.10 and $0.08 per GiB-month, $0.005 per IOPS-month above 3,000, $0.04 per MiB/s-month above 125). Two are recommended; one is rejected for age. This is the script's own output for that recorded evidence; a guided run writes the same values with `run.mode` `guided`. The numbers follow the formulas in `checks.md`, `pricing.md` and `savings.md`; do not reuse them as AWS prices.

```json
{
  "schema_version": "1.0",
  "skill": {
    "id": "co-gp2-to-gp3",
    "revision": "0.3.0",
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
    "discovered_count": 3,
    "assessed_count": 3,
    "unassessed_ids": []
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
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-a"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "fixedVolumeTypes": [
              "gp3"
            ]
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "expectedTypes": [
              "gp2"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "pass",
          "value": {
            "createTime": "2025-09-27T10:15:00.000Z",
            "ageDays": 399,
            "minAgeDays": 7
          }
        },
        {
          "name": "EMRClusterAssociation",
          "status": "pass",
          "value": {
            "attachedInstanceId": "i-example-a"
          }
        },
        {
          "name": "ElasticVolumeEnabled",
          "status": "pass",
          "value": {
            "createTime": "2025-09-27T10:15:00.000Z",
            "elasticSupportDate": "2016-12-03T23:40:00.000Z"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 240.0,
            "annualCost": 2400.0,
            "minimalSavings": 0
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 240.0,
            "annualCost": 2400.0
          }
        }
      ],
      "recommendation": {
        "action": "change_volume_type",
        "parameters": {
          "volume_type": "gp3",
          "size_gib": 2000,
          "iops": 6000,
          "throughput_mibps": 250
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "configuration_projection",
        "currency": "USD",
        "current_annual": 2400.0,
        "target_annual": 2160.0,
        "annual_savings": 240.0
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/1/output/Volumes/0",
        "evidence/describe-instances.json#/0/output/Reservations/0/Instances/0",
        "evidence/calculations.json#/vol-example-a"
      ],
      "details": {
        "savings_calculated": true,
        "finderReportData": {
          "resourceFixedConfiguration": {
            "retypeToClass": "gp3"
          },
          "volumeId": "vol-example-a",
          "volumeSize": 2000,
          "gp2Iops": 6000,
          "gp3Iops": 6000,
          "provisionedIops": 3000,
          "gp2Throughput": 250,
          "gp3Throughput": 250,
          "provisionedThroughput": 125,
          "annualSavings": 240.0,
          "gp2Cost": 2400.0,
          "gp3Cost": 2160.0,
          "savingsPercentage": "10.0",
          "gp2PublicCost": 2400.0,
          "gp3PublicCost": 2160.0,
          "publicSavings": 240.0,
          "publicSavingsPercentage": "10.0",
          "discountRatio": 1.0,
          "gp2StorageRate": "$0.1",
          "gp3StorageRate": "$0.08",
          "gp3IopsRate": "$0.005",
          "gp3ThroughputRate": "$0.04",
          "gp2StorageCost": 2400.0,
          "gp3StorageCost": 1920.0,
          "gp3IopsCost": 180.0,
          "gp3ThroughputCost": 60.0,
          "calculatedGp2Cost": 2400.0,
          "calculatedGp3Cost": 2160.0,
          "hourlyGp2Cost": 0.273972602739726,
          "hourlyGp3Cost": 0.2465753424657534
        }
      }
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
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-b"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "fixedVolumeTypes": [
              "gp3"
            ]
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "expectedTypes": [
              "gp2"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "pass",
          "value": {
            "createTime": "2026-02-24T08:00:00.000Z",
            "ageDays": 249,
            "minAgeDays": 7
          }
        },
        {
          "name": "EMRClusterAssociation",
          "status": "pass",
          "value": {
            "attachedInstanceId": "i-example-b"
          }
        },
        {
          "name": "ElasticVolumeEnabled",
          "status": "pass",
          "value": {
            "createTime": "2026-02-24T08:00:00.000Z",
            "elasticSupportDate": "2016-12-03T23:40:00.000Z"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 36.0,
            "annualCost": 180.0,
            "minimalSavings": 0
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 36.0,
            "annualCost": 180.0
          }
        }
      ],
      "recommendation": {
        "action": "change_volume_type",
        "parameters": {
          "volume_type": "gp3",
          "size_gib": 150,
          "iops": 3000,
          "throughput_mibps": 125
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "public_price",
        "estimation_method": "configuration_projection",
        "currency": "USD",
        "current_annual": 180.0,
        "target_annual": 144.0,
        "annual_savings": 36.0
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/1/output/Volumes/1",
        "evidence/describe-instances.json#/0/output/Reservations/0/Instances/1",
        "evidence/calculations.json#/vol-example-b"
      ],
      "details": {
        "savings_calculated": true,
        "finderReportData": {
          "resourceFixedConfiguration": {
            "retypeToClass": "gp3"
          },
          "volumeId": "vol-example-b",
          "volumeSize": 150,
          "gp2Iops": 450,
          "gp3Iops": 3000,
          "provisionedIops": 0,
          "gp2Throughput": 113,
          "gp3Throughput": 125,
          "provisionedThroughput": 0,
          "annualSavings": 36.0,
          "gp2Cost": 180.0,
          "gp3Cost": 144.0,
          "savingsPercentage": "20.0",
          "gp2PublicCost": 180.0,
          "gp3PublicCost": 144.0,
          "publicSavings": 36.0,
          "publicSavingsPercentage": "20.0",
          "discountRatio": 1.0,
          "gp2StorageRate": "$0.1",
          "gp3StorageRate": "$0.08",
          "gp3IopsRate": "$0.00",
          "gp3ThroughputRate": "$0.00",
          "gp2StorageCost": 180.0,
          "gp3StorageCost": 144.0,
          "gp3IopsCost": 0.0,
          "gp3ThroughputCost": 0.0,
          "calculatedGp2Cost": 180.0,
          "calculatedGp3Cost": 144.0,
          "hourlyGp2Cost": 0.02054794520547945,
          "hourlyGp3Cost": 0.01643835616438356
        }
      }
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
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "vol-example-c"
          }
        },
        {
          "name": "ResourceFixed",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "fixedVolumeTypes": [
              "gp3"
            ]
          }
        },
        {
          "name": "VolumeExpectedType",
          "status": "pass",
          "value": {
            "volumeType": "gp2",
            "expectedTypes": [
              "gp2"
            ]
          }
        },
        {
          "name": "VolumeAgeXDays",
          "status": "fail",
          "value": {
            "createTime": "2026-10-29T06:30:00.000Z",
            "ageDays": 2,
            "minAgeDays": 7
          },
          "reason": "2 days old; the minimum is 7 days."
        },
        {
          "name": "EMRClusterAssociation",
          "status": "pass",
          "value": {}
        },
        {
          "name": "ElasticVolumeEnabled",
          "status": "pass",
          "value": {
            "createTime": "2026-10-29T06:30:00.000Z",
            "elasticSupportDate": "2016-12-03T23:40:00.000Z"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "not_evaluated",
          "value": {
            "annualSavings": 0,
            "annualCost": 120.0,
            "minimalSavings": 0
          },
          "reason": "The finder ran this on its initialized zero savings after the earlier rejection; no saving was calculated."
        },
        {
          "name": "MaximumSavings",
          "status": "not_evaluated",
          "value": {
            "annualSavings": 0,
            "annualCost": 120.0
          },
          "reason": "The finder ran this on its initialized zero savings after the earlier rejection; no saving was calculated."
        }
      ],
      "costs": {
        "status": "not_calculated",
        "basis": "public_price",
        "currency": "USD",
        "current_annual": 120.0
      },
      "evidence_refs": [
        "evidence/describe-volumes.json#/1/output/Volumes/2",
        "evidence/calculations.json#/vol-example-c"
      ],
      "details": {
        "savings_calculated": false,
        "finderReportData": {}
      }
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
    "estimation_method": "configuration_projection",
    "current_annual": 2580.0,
    "target_annual": 2304.0
  },
  "issues": [],
  "qualifications": [
    "Public prices; the current cost is a public-price illustration of the volume's size, not a billed cost.",
    "CloudFix recommendation history was not queried; the finder's RecommendationFinalized row rests on an empty local-adapter assumption.",
    "Unchecked pipeline context: finalized recommendation history, tenant settings/discovery threshold, CUR discovery parity, pipeline persistence/monitoring and change-request revalidation.",
    "Volumes were discovered with a gp2 volume inventory, not CloudFix's billing-data discovery; its cost threshold, region set and tag exclusions were not checked.",
    "The finder does not test workload performance or utilization; workload safety has not been demonstrated.",
    "One-time change costs and first-year savings were not established."
  ],
  "verification": {
    "method": "self_check",
    "summary": "Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. The sizing and cost expressions are recorded per volume in evidence/calculations.json. AWS facts were not re-read."
  },
  "evidence_refs": [
    "evidence/identity.json#/0",
    "evidence/pricing.json",
    "evidence/describe-volumes.json",
    "evidence/describe-instances.json"
  ],
  "details": {
    "run_metadata": {
      "accountId": "000000000000",
      "region": "us-east-1",
      "asOf": "2026-11-01T00:00:00.000Z",
      "configuration": {
        "volumeAgeThreshold": 7,
        "minimalSavings": 0
      },
      "configuration_overrides": {},
      "run_classification": "standard",
      "experiment": null
    }
  },
  "execution_status": "not_executed"
}
```

A volume that could not be resolved, and the issue that says why and what to read next. Here recorded evidence was supplied without the instance response:

```json
{
  "finding": {
    "id": "vol-example-d",
    "resource": {"id": "vol-example-d", "type": "AWS::EC2::Volume", "region": "us-east-1"},
    "decision": "needs_evidence",
    "checks": [
      {"name": "ResourceExists", "status": "pass", "value": {"resourceId": "vol-example-d"}},
      {"name": "ResourceFixed", "status": "pass", "value": {"volumeType": "gp2", "fixedVolumeTypes": ["gp3"]}},
      {"name": "VolumeExpectedType", "status": "pass", "value": {"volumeType": "gp2", "expectedTypes": ["gp2"]}},
      {"name": "VolumeAgeXDays", "status": "pass", "value": {"createTime": "2026-08-03T12:00:00.000Z", "ageDays": 89, "minAgeDays": 7}},
      {"name": "EMRClusterAssociation", "status": "not_evaluated", "reason": "The DescribeInstances response for i-example-d was not supplied."},
      {"name": "ElasticVolumeEnabled", "status": "not_evaluated", "reason": "Not run: the assessment stopped at the missing instance response."},
      {"name": "MinimalSavings", "status": "not_evaluated", "reason": "Not run: the assessment stopped at the missing instance response."},
      {"name": "MaximumSavings", "status": "not_evaluated", "reason": "Not run: the assessment stopped at the missing instance response."}
    ],
    "costs": {"status": "not_calculated"},
    "evidence_refs": ["evidence/describe-volumes.json#/1/output/Volumes/0"],
    "details": {"savings_calculated": false, "finderReportData": {}}
  },
  "issue": {
    "kind": "missing_evidence", "stage": "collection", "resource_id": "vol-example-d",
    "message": "The DescribeInstances response for the attached instance was not supplied.",
    "request": {"kind": "aws_read", "name": "DescribeInstances", "service": "ec2", "region": "us-east-1", "parameters": {"Filters": [{"Name": "instance-id", "Values": ["i-example-d"]}]}}
  }
}
```

Such a run is `partial`, lists `vol-example-d` in `coverage.unassessed_ids`, and its total is a subtotal. A denied or failed Cost Explorer read gives decision `error` and issue kind `read_error` instead; a denied or failed volume or instance read fails the run.

## Totals

`totals.decision_counts` counts rows by decision. `totals.annual_savings` is the one headline number: the sum of every recommended row's unrounded `annual_savings`, with their shared `currency`, `basis` and `estimation_method`. Add `current_annual` and `target_annual` the same way. Sum with a calculator or script; never add rounded display values. On a partial run this is a subtotal for the volumes assessed so far.

With no recommended rows the total is null, not zero; leave out the current and target totals too. If recommended rows ever differ in currency, price source or method, set `annual_savings` to null and give `reason_not_totaled`. Rows that are not recommended never contribute, whatever amounts they keep. Keep a separately calculated public-price total in the top-level `details.public_price_totals`, never in `totals`.

## Customer report

Write the report from `assessment.json` only. Lead with the result:

**Estimated savings: $276.00/year across two volumes.**

On a partial run call it a subtotal for the assessed scope. Without a total, say how many opportunities were found, or "Savings not established." A completed empty inventory says "No GP2 volumes found, nothing to optimize"; a failed inventory does not.

Next, one context line: account, region, requested scope, volumes discovered, assessed and unassessed, run status, observation time and verification method. Unknown counts stay unknown. Never call a selection an account-wide assessment. Say whether the evidence was read live or supplied (recorded or simulated), and never present recorded evidence as current. Name the run classification and any experiment or configuration override.

Account 000000000000 · us-east-1 · all gp2 volumes · 3 discovered, 3 assessed, 0 unassessed · complete · observed 2026-11-01T00:00:00.000Z · self-check.

Then one plain-language caption for the prices: "AWS public prices" for `public_price`, "account and region effective billing rate" for `account_effective_rate` with its billing period, "supplied prices" for `caller_supplied`; then "estimate for the proposed configuration". Each volume keeps its size.

AWS public prices; estimate for the proposed configuration. Currency: USD.

| Resource | Region | Proposed change | Current per year | After per year | Savings per year |
| --- | --- | --- | ---: | ---: | ---: |
| vol-example-a | us-east-1 | gp3, 2000 GiB, 6,000 IOPS, 250 MiB/s | $2400.00 | $2160.00 | $240.00 |
| vol-example-b | us-east-1 | gp3, 150 GiB, 3,000 IOPS, 125 MiB/s | $180.00 | $144.00 | $36.00 |
| **Total: 2 volumes** | | | **$2580.00** | **$2304.00** | **$276.00** |

One row per recommended volume, largest savings first, then the total. Round only for display, to two decimals. Use "—" for unknown and `$0` only for an established zero. Above 25 rows, show the 25 largest, state how many more are in `assessment.json`, and keep the total over all of them.

Every other volume appears once in a second table:

| Resource | Region | Outcome | Reason / what is needed |
| --- | --- | --- | --- |
| vol-example-c | us-east-1 | Not recommended | 2 days old; the minimum is 7 days. |

Use "Not recommended", "More evidence needed" or "Assessment error", with the exact next read or input for the last two. Give the finder's rejecting check as the reason, not a savings check that only ran on initialized zero savings. A volume rejected before the savings calculation has an unknown saving, never "saves $0". Volumes that share one reason may be grouped on one line with their count.

Close with the qualifications, any run-level failures and links to `assessment.json` and the evidence. State that nothing was executed and that these are estimates, not realized savings.

The original CloudFix report includes volume/type/size, current and target annual cost, savings, public storage/IOPS/throughput cost breakdown, included and additional performance amounts, rates and discount ratio. Preserve these decision-relevant facts in `details.finderReportData` and show them when asked. It also asserts better/equivalent performance, no impact/zero downtime and easy rollback. **Those statements are template text, not facts established by the two AWS reads.** Do not present them as verified guarantees or label estimated costs “Your Actual Cost.”

Charts are optional: current versus after cost, or savings by volume, drawn from the same rows. No chart library or renderer is required.

End the report with: "For more AWS optimization options, see [CloudFix](https://cloudfix.com/)."

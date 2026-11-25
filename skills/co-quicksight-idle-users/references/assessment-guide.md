# Assessment guide: QuickSight idle users

Read this in stage 1 of `guided.md` for the evidence file names and the result shape. It fixes this skill's literal values, explains the fields of `assessment.json`, and shows the customer report. The schema is `assessment-v1.schema.json` beside this file. A file that satisfies the schema is well formed; that does not make its AWS facts or decisions right.

An omitted or null value means unknown. Never write `0` for unknown.

## Contents

- Fixed values
- Field meanings
- Coverage when the whole user list is read
- Decisions
- Filled example
- An unresolved row and its issue
- Denied user inventory
- Totals
- Customer report

## Fixed values

Use these literals exactly. Do not invent other names.

| Value | Literal for this skill |
| --- | --- |
| `schema_version` | `"1.0"` |
| `skill.id` | `co-quicksight-idle-users` |
| `skill.revision` | The `metadata.version` in the `SKILL.md` frontmatter |
| `run.mode` | `local_code` when `scripts/assess.py` produced the result; `guided` when the procedure in `references/guided.md` did |
| `resource.type` | `AWS::QuickSight::User` |
| `resource.id` and `findings[].id` | The QuickSight user ARN, one row per user |
| `resource.region` | The region inside the user ARN |
| `recommendation.action` | `grant_owner_permissions_and_delete_user` |
| `recommendation.parameters` keys | `fromPrincipalId` (the idle user's PrincipalId), `toUserArn` (the receiving user), `analyses`, `dashboards`, `dataSets`, `dataSources` |
| Check names, in order | `RecommendationFinalized`, `ResourceExists`, `ValidateUserRole`, `UserNotProtected`, `UserNotActive`, `HasReceiver`, `MinimalSavings`, `MaximumSavings`, then the opt-out gate `DontFixItOptOut` |
| `execution_status` | `not_executed` |

The action name describes a proposal only: grant the receiver owner-level permissions on the four listed asset arrays, then delete the user. It is not a complete asset transfer, and the assessment never performs it.

| `scope.pricing_mode` | When | `costs.basis` | `costs.estimation_method` |
| --- | --- | --- | --- |
| `supplied` | The customer gave a per-user annual cost | `caller_supplied` | `configuration_projection` |
| `billing` | Billed costs were requested. The default: Cost Explorer, read as `billing-cost-explorer.md` specifies | `account_effective_rate`, with the Cost Explorer window as `source_period` and the qualification below | `configuration_projection` |
| `billing` | Billed costs were requested and the customer supplied a CUR table, read as `billing-cur.md` specifies | `resource_billed`, with the frozen query bounds as `source_period` | `configuration_projection` |
| `public_prices` | An explicitly requested subscription quotation, with edition, plan, role and billable quantity established | `public_price` | `configuration_projection` |
| `technical_only` | No cost was requested | none: `costs` is `{"status":"not_calculated"}` | none |

In every priced mode `current_annual` is `resource.annualCost`, `annual_savings` is the finder's assignment `annualSavings = resource.annualCost`, and `target_annual` is `projectedAnnualCost`. Only a `recommended` row has `costs.status:"calculated"`. Every other row is `not_calculated`: it keeps a known `current_annual` with its `basis` and `currency`, and the formula results go in `details.annualSavings` and `details.projectedAnnualCost`, outside the savings total. An unknown plan or entitlement, or a requested cost that was never obtained, leaves every amount out; conditional quotes for candidate plans go in `details.conditional_quotes`, never in `costs`. A current cost is known when it needs no further read: a supplied cost naming the user, or the established Cost Explorer rate for the user's subscription kind. Carry it on every row that has one, including a row stopped by an earlier check; a row with no known current cost carries `{"status": "not_calculated"}` only.

An `account_effective_rate` amount is the account's effective rate for the user's subscription kind over the window, not that user's own invoice line, and every such row says so in `costs.qualifications`. The amounts in this fragment are synthetic:

```json
{
  "costs": {
    "status": "calculated",
    "basis": "account_effective_rate",
    "estimation_method": "configuration_projection",
    "currency": "USD",
    "source_period": {
      "start": "2026-09-28T00:00:00Z",
      "end": "2026-10-26T00:00:00Z"
    },
    "current_annual": 238.22,
    "target_annual": 0.0,
    "annual_savings": 238.22,
    "qualifications": [
      "Account-level effective rate for the author subscription kind from Cost Explorer over the source period (21.6 USD per user-month; one subscription's cost over the window, annualized as the finder does: sum / 30 * 365). It is not this user's own invoice line."
    ]
  },
  "details": {
    "subscription_kind": "author",
    "effective_monthly_rate": 21.6
  }
}
```

Billed and public rows are never mixed in one run.

Names inside `details` are fixed too. On the top level: `finderId` (`QsRemoveIdleUser`), `namespace` (`"default"`), `discovery_method` (`explicit_selection`, `list_users` or `cur`), `activity_mode` (`fresh` or `cache_replay`), `activity_regions`, `configuration` (`daysForIdleUser`, `protectedUsersPattern`, `minimalSavings`), `evaluation_timezone`, `users_listed`, `users_dropped`, `allowed_role_users_scanned` and `framework_context_complete`; in billing mode also `billing_method` (`cost_explorer` or `cur`). The complete `run_metadata` saved in stage 1 stays in the evidence and is referenced from the top-level `evidence_refs`. On a finding: `userName`, `role`, `identityType`, `activity_evidence_confidence`, `receiver`, `opt_out` (every observation, with supersession evidence), `resourceTags`, with a Cost Explorer rate `subscription_kind` and `effective_monthly_rate`, and on rows that are not recommended `candidate_parameters`, `annualSavings` and `projectedAnnualCost` where known.

## Field meanings

| Field | Meaning |
| --- | --- |
| `skill.source_ref` | The fetched commit or file hash of the skill, or null. Never invented. |
| `run.observed_at` | The frozen `asOf`, in UTC. |
| `run.status` | `complete`: every user in scope has a final decision, including rejections and a completed empty inventory. `partial`: at least one user in scope is unresolved. `failed`: an error prevented the assessment, for example identity or `ListUsers` failed, so no user could be assessed. |
| `scope` | `account_id` is the account assessed (null only when identity could not be established). `regions` holds the selected QuickSight region(s). `resource_ids` is the explicit list of user ARNs, or null to discover every user. `pricing_mode` is what the customer asked for. |
| `coverage` | See the next section. |
| `decision` | `recommended`, `not_recommended`, `needs_evidence` or `error`; see Decisions. |
| `technical_eligibility` | Optional. Omit it on `recommended` rows. Add it only when it says more than the decision: `eligible` on a `not_recommended` row rejected only by the opt-out gate, a savings guard or `RecommendationFinalized`, or on an unresolved row held back only by missing economics; `unknown` on another unresolved row. |
| `checks` | The nine names above, in order. `status` is `pass`, `fail` or `not_evaluated`; anything but `pass` carries a `reason`. `value` is the native row value from the validation table. Add `native_approved` only where the finder's own result differs from `status`. |
| `recommendation` | `action` and `parameters`, on recommended rows only. |
| `costs` | As in the pricing table above. Keep unrounded values. `one_time_cost` and `first_year_savings` are left out unless independently established. |
| `qualifications` | Run-wide limits once at the top level; row-specific limits on the row's `costs`. |
| `evidence_refs` | `path#/json/pointer`, relative to `run.evidence_root` (default: the folder holding `assessment.json`). Required on recommended rows. The top-level list holds run-wide reads such as identity, regions, the user list and the probes. |
| `issues` | What is missing, failed or worth flagging. Every `needs_evidence` or `error` row has one, and its `request` names the exact next read or input. One issue without a `resource_id` covers a failure that affects many users. |
| `verification` | `method` is `self_check`, `independent_review` or `none`; `summary` says what was and was not checked. Never record a review that did not happen. |
| `source_output` | Optional. Only when the customer asked for the finder-shaped projection in `native-projection.md`. |

Rows also keep evidence references for every rule, skill version, any supplied source revision, input/currency/cost basis, computations, all missing/skipped checks, inventory/namespace/region coverage, role/protection/identity caveats, `activity_evidence_confidence`, full opt-out observations and supersession evidence, activity reasons, receiver and full asset lists. Distinguish native empty-output inference after swallowed faults from a verified empty response. Report annual cost/savings/projected cost only when justified. Never drop a fact to fit the format: put it in `details`.

Evidence files, under `evidence/` beside `assessment.json`: `run-metadata.json`, `get-caller-identity.json`, `list-users.json`, `describe-regions.json`, `activity-probes-<region>.json` and `activity-walks-<region>.json` per region, `user-<n>.json` (the tag read and the four asset searches of the n-th user in scope), `cost-explorer.json` (every Cost Explorer request, page and error), `supplied-costs.json`, `calculations.json` (the activity summaries, the cost expressions and the receiver with its rule: the latest event, and on equal times the user listed later); a live run adds `timings.json` with the wall time of every walk. The guided path uses the same names and this layout, which a replay by the script needs:

- `run-metadata.json` is `{run_metadata: {finderId, accountId, region, asOf, evaluation_timezone, configuration, configuration_overrides, run_classification, experiment}, request: {pricing_mode, resource_ids, profile, billing_profile}}`.
- Each read file is a JSON array of read records in the order made, one per page, retry or failure: `{service, operation, region, profile, input, observed_at, output}`, with the request exactly as sent in `input` and the region it was sent to. A failed read holds `error: {code, message}` in place of `output`.
- `cost-explorer.json` starts with the `sts` `GetCallerIdentity` record of the billing caller.

## Coverage when the whole user list is read

The finder always lists every user in the default namespace and walks activity for every allowed-role user, even when only a few users were selected, because existence (check 2) and the receiver (check 6) depend on that full scan. Those reads support checks. They are evidence, not coverage.

`coverage` counts distinct users in the requested scope only:

- **Explicit selection** (`scope.resource_ids` lists user ARNs): only the selected users count. `discovered_count` is the number selected, each selected user has exactly one row, and a selected ARN missing from the normalized list is a row rejected by `ResourceExists`. Save the full list and scan as evidence and summarize them in the top-level `details` (`users_listed`, `allowed_role_users_scanned`, `activity_regions`). Selecting 2 users while listing 40 gives `discovered_count: 2`. Never call a selection an account-wide assessment.
- **Discovery** (`scope.resource_ids` is null): every distinct user ARN in the normalized list is in scope and has one row. Ordinary `READER` users are rows rejected by `ValidateUserRole`. With native CUR discovery the users in scope are the groups the CUR query returned. Records dropped from the normalized list are kept in evidence, counted in `details.users_dropped` and named in a `qualification` issue.
- `assessed_count` counts users with a final decision (`recommended` or `not_recommended`); `unassessed_ids` lists the ARNs of the rest.
- `inventory_complete` is true only when every `ListUsers` page of the default namespace was read. A denied, missing or unfinished list is `false` with `discovered_count: null`. Missing is not zero.
- Other namespaces are outside the assessment; say so in `qualifications`.

## Decisions

Apply in order:

1. A completed rejecting check gives `not_recommended`, even if other reads are missing or failed. Keep its reason and any known cost.
2. Otherwise a required read or calculation that failed gives `error`, and a required input that is absent or incomplete gives `needs_evidence`.
3. Only complete, passing checks for the requested scope and pricing mode give `recommended`. A priced request needs calculated savings; `technical_only` does not. Framework history that a standalone run cannot see is a stated qualification: `RecommendationFinalized` stays `not_evaluated`, never a fabricated pass.

| Situation | `decision` | How the checks record it |
| --- | --- | --- |
| Checks 2–6 pass, the opt-out gate is clear, and in a priced mode the cost is established and checks 7 and 8 pass | `recommended` | `RecommendationFinalized` is `pass` with supplied history, otherwise `not_evaluated`. In `technical_only` checks 7 and 8 are `not_evaluated` and the total is null with `reason_not_totaled` |
| Known nonempty `cloudfix:dontFixIt`, including `"false"`, `"0"`, `"no"` or whitespace | `not_recommended` | `DontFixItOptOut: fail` with the observed value; native rows kept as evaluated |
| Absent from the normalized list, role not allowed (ordinary `READER`), or protection pattern matched | `not_recommended` | The fail-fast check is `fail`; later checks are `not_evaluated` |
| `UserNotActive` reason `activity-identified` | `not_recommended` | `UserNotActive: fail` with the event time; later checks `not_evaluated` |
| A reliable user-resolved recent-use event outside the native predicates | `not_recommended` | `UserNotActive: fail`, `native_approved: true` |
| No receiver | `not_recommended` | `HasReceiver: fail`; savings checks `not_evaluated` |
| Receiver unknown: the candidate passes check 5, but a walk of another scanned user (not the chosen receiver) failed, is missing or did not reach its last page | `error` when that walk failed or was denied, otherwise `needs_evidence` | `HasReceiver: not_evaluated`; no `recommendation` and no `details.receiver`; one issue on the row names the unread walk as the next read |
| `MinimalSavings`, `MaximumSavings` or `RecommendationFinalized` fails | `not_recommended` | The failing check is `fail`; these are ordinary failures, so later checks still run |
| `UserNotActive` reason `no-region-with-event-source`, `activity-not-examined` or `activity-not-fully-read` | `needs_evidence`, or `error` when a read failed or was denied | `UserNotActive: not_evaluated` with that reason, `native_approved: false`. A coverage decline is not a finding that the user is active |
| Native no-match for IAM Identity Center or another sign-in event model the lookup is not shown to observe | `needs_evidence` | `UserNotActive: not_evaluated`, `native_approved: true`; `details.activity_evidence_confidence` says why |
| Invalid `protectedUsersPattern` | `needs_evidence` | `UserNotProtected: not_evaluated`, `native_approved: true`; never repair the pattern |
| Opt-out tag evidence failed, missing, unattempted, incomplete or in unresolved conflict | `error` for a failed read, otherwise `needs_evidence` | `DontFixItOptOut: not_evaluated` |
| An asset search threw | `error` | Checks through `HasReceiver` as evaluated; later checks `not_evaluated`; never an empty asset list |
| A probe or walk failed or was denied | `error` for each affected user | `UserNotActive: not_evaluated`; one issue can cover them all |
| Requested cost absent, or its billing read failed | `needs_evidence`, or `error` for the failed read | Checks 7 and 8 `not_evaluated`; `costs` is `not_calculated` |
| A Cost Explorer request denied or failed, or its response unusable | `error` | Checks 7 and 8 `not_evaluated`; `costs` is `not_calculated`; one `read_error` (or `calculation_error`) issue naming the exact request. Never a public price |
| Cost Explorer shows no usable usage for the user's subscription kind: no `QS-User-` operation, no user-month unit, no mapped usage type or zero user-months | `needs_evidence` | Checks 7 and 8 `not_evaluated`; `costs` is `not_calculated`; a `missing_evidence` issue. Users of a kind that has a rate are still priced |
| `ListUsers` denied | No rows for users never listed; explicitly selected users are `error` rows | No later read is made. See the denied example below |
| Every due walk reached its last page with no usable match, but a matching event had no `EventTime`, or a region's probe returned no `Events` list or an event without `EventTime` | `error` | `UserNotActive: not_evaluated`, `native_approved: true`, value reason `no-activity-identified`; one `read_error` issue on the row for each such walk or probe |

A `complete` run contains only `recommended` and `not_recommended` rows.

## Filled example

Four users discovered, supplied prices, a complete run: one recommended, one rejected by the opt-out gate, one active (the receiver) and one ordinary `READER`. Identities and amounts are illustrative; they are not AWS quotes. This is the saved result of the recorded case the script's tests replay.

```json
{
  "schema_version": "1.0",
  "skill": {
    "id": "co-quicksight-idle-users",
    "revision": "0.4.0",
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
    "pricing_mode": "supplied"
  },
  "coverage": {
    "inventory_complete": true,
    "discovered_count": 4,
    "assessed_count": 4,
    "unassessed_ids": []
  },
  "findings": [
    {
      "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a",
      "resource": {
        "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a",
        "type": "AWS::QuickSight::User",
        "region": "us-east-1"
      },
      "decision": "recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "No framework history was supplied; a standalone run cannot see it."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a"
          }
        },
        {
          "name": "ValidateUserRole",
          "status": "pass",
          "value": {
            "role": "AUTHOR"
          }
        },
        {
          "name": "UserNotProtected",
          "status": "pass",
          "value": {
            "userName": "analyst-a"
          }
        },
        {
          "name": "UserNotActive",
          "status": "pass",
          "value": {
            "finishedProcessing": true,
            "reason": "no-activity-identified",
            "windowDays": 30
          }
        },
        {
          "name": "HasReceiver",
          "status": "pass",
          "value": {
            "receiverArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 300,
            "annualCost": 300,
            "minimalSavings": 0
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 300,
            "annualCost": 300
          }
        },
        {
          "name": "DontFixItOptOut",
          "status": "pass",
          "value": {
            "key": "cloudfix:dontFixIt",
            "observed": null
          }
        }
      ],
      "recommendation": {
        "action": "grant_owner_permissions_and_delete_user",
        "parameters": {
          "fromPrincipalId": "federated/iam/AIDAEXAMPLEANALYSTA",
          "toUserArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
          "analyses": [
            "analysis-1"
          ],
          "dashboards": [
            "dashboard-1"
          ],
          "dataSets": [],
          "dataSources": []
        }
      },
      "costs": {
        "status": "calculated",
        "basis": "caller_supplied",
        "estimation_method": "configuration_projection",
        "currency": "USD",
        "current_annual": 300,
        "target_annual": 0,
        "annual_savings": 300
      },
      "evidence_refs": [
        "evidence/list-users.json#/0/output/UserList/0",
        "evidence/activity-walks-us-east-1.json#/0",
        "evidence/activity-walks-us-west-2.json#/0",
        "evidence/user-0.json",
        "evidence/calculations.json#/users/arn:aws:quicksight:us-east-1:000000000000:user~1default~1analyst-a"
      ],
      "details": {
        "userName": "analyst-a",
        "role": "AUTHOR",
        "identityType": "IAM",
        "activity_evidence_confidence": "IAM identity; the Username lookup is shown to observe this user's quicksight.amazonaws.com events; 2 of 2 due regions read to their last page or to a matching event.",
        "receiver": {
          "arn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
          "userName": "admin-r",
          "eventTime": "2026-10-30T09:00:00.000Z"
        },
        "resourceTags": {}
      }
    },
    {
      "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-b",
      "resource": {
        "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-b",
        "type": "AWS::QuickSight::User",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "No framework history was supplied; a standalone run cannot see it."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-b"
          }
        },
        {
          "name": "ValidateUserRole",
          "status": "pass",
          "value": {
            "role": "AUTHOR"
          }
        },
        {
          "name": "UserNotProtected",
          "status": "pass",
          "value": {
            "userName": "analyst-b"
          }
        },
        {
          "name": "UserNotActive",
          "status": "pass",
          "value": {
            "finishedProcessing": true,
            "reason": "no-activity-identified",
            "windowDays": 30
          }
        },
        {
          "name": "HasReceiver",
          "status": "pass",
          "value": {
            "receiverArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r"
          }
        },
        {
          "name": "MinimalSavings",
          "status": "pass",
          "value": {
            "annualSavings": 300,
            "annualCost": 300,
            "minimalSavings": 0
          }
        },
        {
          "name": "MaximumSavings",
          "status": "pass",
          "value": {
            "annualSavings": 300,
            "annualCost": 300
          }
        },
        {
          "name": "DontFixItOptOut",
          "status": "fail",
          "value": {
            "key": "cloudfix:dontFixIt",
            "observed": "false"
          },
          "reason": "The tag value \"false\" is a nonempty string, so the user is opted out. It is not trimmed or read as boolean false."
        }
      ],
      "technical_eligibility": "eligible",
      "costs": {
        "status": "not_calculated",
        "basis": "caller_supplied",
        "currency": "USD",
        "current_annual": 300
      },
      "evidence_refs": [
        "evidence/list-users.json#/0/output/UserList/1",
        "evidence/activity-walks-us-east-1.json#/2",
        "evidence/activity-walks-us-west-2.json#/1",
        "evidence/user-1.json",
        "evidence/calculations.json#/users/arn:aws:quicksight:us-east-1:000000000000:user~1default~1analyst-b"
      ],
      "details": {
        "userName": "analyst-b",
        "role": "AUTHOR",
        "identityType": "IAM",
        "activity_evidence_confidence": "IAM identity; the Username lookup is shown to observe this user's quicksight.amazonaws.com events; 2 of 2 due regions read to their last page or to a matching event.",
        "receiver": {
          "arn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
          "userName": "admin-r",
          "eventTime": "2026-10-30T09:00:00.000Z"
        },
        "resourceTags": {
          "cloudfix:dontFixIt": "false",
          "team": "finance"
        },
        "opt_out": [
          {
            "source": "evidence/user-1.json#/0",
            "value": "false",
            "observed_at": "2026-11-01T00:00:05Z"
          }
        ],
        "annualSavings": 300,
        "projectedAnnualCost": 0,
        "candidate_parameters": {
          "fromPrincipalId": "federated/iam/AIDAEXAMPLEANALYSTB",
          "toUserArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
          "analyses": [],
          "dashboards": [
            "dashboard-2"
          ],
          "dataSets": [],
          "dataSources": []
        }
      }
    },
    {
      "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
      "resource": {
        "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
        "type": "AWS::QuickSight::User",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "No framework history was supplied; a standalone run cannot see it."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r"
          }
        },
        {
          "name": "ValidateUserRole",
          "status": "pass",
          "value": {
            "role": "ADMIN"
          }
        },
        {
          "name": "UserNotProtected",
          "status": "pass",
          "value": {
            "userName": "admin-r"
          }
        },
        {
          "name": "UserNotActive",
          "status": "fail",
          "value": {
            "finishedProcessing": true,
            "eventTime": "2026-10-30T09:00:00.000Z",
            "reason": "activity-identified",
            "windowDays": 30
          },
          "reason": "Activity was identified at 2026-10-30T09:00:00.000Z."
        },
        {
          "name": "HasReceiver",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
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
        },
        {
          "name": "DontFixItOptOut",
          "status": "not_evaluated",
          "reason": "Not needed after the earlier rejection."
        }
      ],
      "costs": {
        "status": "not_calculated",
        "basis": "caller_supplied",
        "currency": "USD",
        "current_annual": 300
      },
      "evidence_refs": [
        "evidence/list-users.json#/0/output/UserList/2",
        "evidence/calculations.json#/users/arn:aws:quicksight:us-east-1:000000000000:user~1default~1admin-r"
      ],
      "details": {
        "userName": "admin-r",
        "role": "ADMIN",
        "identityType": "IAM",
        "activity_evidence_confidence": "IAM identity; the Username lookup is shown to observe this user's quicksight.amazonaws.com events; 2 of 2 due regions read to their last page or to a matching event.",
        "annualSavings": 300,
        "projectedAnnualCost": 0
      }
    },
    {
      "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/viewer-d",
      "resource": {
        "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/viewer-d",
        "type": "AWS::QuickSight::User",
        "region": "us-east-1"
      },
      "decision": "not_recommended",
      "checks": [
        {
          "name": "RecommendationFinalized",
          "status": "not_evaluated",
          "reason": "No framework history was supplied; a standalone run cannot see it."
        },
        {
          "name": "ResourceExists",
          "status": "pass",
          "value": {
            "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/viewer-d"
          }
        },
        {
          "name": "ValidateUserRole",
          "status": "fail",
          "value": {
            "role": "READER"
          },
          "reason": "READER is not one of AUTHOR, ADMIN, AUTHOR_PRO, ADMIN_PRO, READER_PRO."
        },
        {
          "name": "UserNotProtected",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
        },
        {
          "name": "UserNotActive",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
        },
        {
          "name": "HasReceiver",
          "status": "not_evaluated",
          "reason": "Not run after the earlier rejection."
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
        },
        {
          "name": "DontFixItOptOut",
          "status": "not_evaluated",
          "reason": "Not needed after the earlier rejection."
        }
      ],
      "costs": {
        "status": "not_calculated"
      },
      "evidence_refs": [
        "evidence/list-users.json#/0/output/UserList/3",
        "evidence/calculations.json#/users/arn:aws:quicksight:us-east-1:000000000000:user~1default~1viewer-d"
      ],
      "details": {
        "userName": "viewer-d",
        "role": "READER",
        "identityType": "IAM"
      }
    }
  ],
  "totals": {
    "decision_counts": {
      "recommended": 1,
      "not_recommended": 3,
      "needs_evidence": 0,
      "error": 0
    },
    "annual_savings": 300,
    "currency": "USD",
    "basis": "caller_supplied",
    "estimation_method": "configuration_projection",
    "current_annual": 300,
    "target_annual": 0
  },
  "issues": [],
  "qualifications": [
    "Current costs were supplied by the caller (finance export) and used unchanged.",
    "Only the default namespace was assessed. No activity identified under the finder's predicates is not proof that deletion is safe.",
    "Framework history was not supplied, so RecommendationFinalized was not evaluated. This is a standalone total, not a native-complete one.",
    "The finder counts any CloudTrail event under the user's name as activity, including API reads made by the identity that runs this assessment or an earlier test; such a user is not idle under this rule, and can be the receiver.",
    "The proposal for a recommended user is to grant the receiver owner-level permissions on the listed analyses, dashboards, data sets and data sources, then delete the user by principal ID. It is not a complete asset transfer; owner intent, receiver suitability and retention needs were not established.",
    "Savings are a conditional gross subscription saving. Verified marginal account savings are unknown: commitments, proration, consolidated licensing and remaining charges were not resolved. One-time costs and first-year savings are unknown."
  ],
  "verification": {
    "method": "self_check",
    "summary": "Recomputed the decision counts and the unrounded totals from the saved rows and opened every evidence reference after writing the files. The activity summaries, receiver choice and cost expressions are recorded in evidence/calculations.json. AWS facts were not re-read."
  },
  "evidence_refs": [
    "evidence/run-metadata.json",
    "evidence/get-caller-identity.json",
    "evidence/list-users.json",
    "evidence/describe-regions.json",
    "evidence/activity-probes-us-east-1.json",
    "evidence/activity-probes-us-west-2.json"
  ],
  "details": {
    "finderId": "QsRemoveIdleUser",
    "namespace": "default",
    "discovery_method": "list_users",
    "activity_mode": "fresh",
    "configuration": {
      "daysForIdleUser": 30,
      "protectedUsersPattern": null,
      "minimalSavings": 0
    },
    "evaluation_timezone": "UTC",
    "framework_context_complete": false,
    "users_listed": 4,
    "users_dropped": 0,
    "allowed_role_users_scanned": 3,
    "activity_regions": [
      "us-east-1",
      "us-west-2"
    ]
  },
  "execution_status": "not_executed"
}
```

A fail-fast rejection stops the later checks. An ordinary `READER`:

```json
{
  "decision": "not_recommended",
  "checks": [
    {
      "name": "RecommendationFinalized",
      "status": "not_evaluated",
      "reason": "No framework history was supplied; a standalone run cannot see it."
    },
    {
      "name": "ResourceExists",
      "status": "pass",
      "value": {
        "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/viewer-d"
      }
    },
    {
      "name": "ValidateUserRole",
      "status": "fail",
      "value": {
        "role": "READER"
      },
      "reason": "READER is not one of AUTHOR, ADMIN, AUTHOR_PRO, ADMIN_PRO, READER_PRO."
    },
    {
      "name": "UserNotProtected",
      "status": "not_evaluated",
      "reason": "Not run after the earlier rejection."
    },
    {
      "name": "UserNotActive",
      "status": "not_evaluated",
      "reason": "Not run after the earlier rejection."
    },
    {
      "name": "HasReceiver",
      "status": "not_evaluated",
      "reason": "Not run after the earlier rejection."
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
    },
    {
      "name": "DontFixItOptOut",
      "status": "not_evaluated",
      "reason": "Not needed after the earlier rejection."
    }
  ],
  "costs": {
    "status": "not_calculated"
  }
}
```

## An unresolved row and its issue

An IAM Identity Center user with no matching event. The finder's own check passes, but the lookup is not shown to observe this user's sign-ins, so inactivity is unknown:

```json
{
  "finding": {
    "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a",
    "resource": {
      "id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a",
      "type": "AWS::QuickSight::User",
      "region": "us-east-1"
    },
    "decision": "needs_evidence",
    "checks": [
      {
        "name": "RecommendationFinalized",
        "status": "not_evaluated",
        "reason": "No framework history was supplied; a standalone run cannot see it."
      },
      {
        "name": "ResourceExists",
        "status": "pass",
        "value": {
          "resourceId": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a"
        }
      },
      {
        "name": "ValidateUserRole",
        "status": "pass",
        "value": {
          "role": "AUTHOR"
        }
      },
      {
        "name": "UserNotProtected",
        "status": "pass",
        "value": {
          "userName": "analyst-a"
        }
      },
      {
        "name": "UserNotActive",
        "status": "not_evaluated",
        "native_approved": true,
        "value": {
          "finishedProcessing": true,
          "windowDays": 30,
          "reason": "no-activity-identified"
        },
        "reason": "IAM_IDENTITY_CENTER identity: the Username lookup is not shown to observe this user's sign-in events, so an empty result does not establish inactivity."
      },
      {
        "name": "HasReceiver",
        "status": "pass",
        "value": {
          "receiverArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r"
        }
      },
      {
        "name": "MinimalSavings",
        "status": "pass",
        "value": {
          "annualSavings": 300,
          "annualCost": 300,
          "minimalSavings": 0
        }
      },
      {
        "name": "MaximumSavings",
        "status": "pass",
        "value": {
          "annualSavings": 300,
          "annualCost": 300
        }
      },
      {
        "name": "DontFixItOptOut",
        "status": "pass",
        "value": {
          "key": "cloudfix:dontFixIt",
          "observed": null
        }
      }
    ],
    "technical_eligibility": "unknown",
    "costs": {
      "status": "not_calculated",
      "basis": "caller_supplied",
      "currency": "USD",
      "current_annual": 300
    },
    "evidence_refs": [
      "evidence/list-users.json#/0/output/UserList/0",
      "evidence/activity-walks-us-east-1.json#/0",
      "evidence/user-0.json",
      "evidence/calculations.json#/users/arn:aws:quicksight:us-east-1:000000000000:user~1default~1analyst-a"
    ],
    "details": {
      "userName": "analyst-a",
      "role": "AUTHOR",
      "identityType": "IAM_IDENTITY_CENTER",
      "activity_evidence_confidence": "Unverified event model; native no-match only.",
      "receiver": {
        "arn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
        "userName": "admin-r",
        "eventTime": "2026-10-30T09:00:00.000Z"
      },
      "resourceTags": {},
      "annualSavings": 300,
      "projectedAnnualCost": 0,
      "candidate_parameters": {
        "fromPrincipalId": "federated/iam/AIDAEXAMPLEANALYSTA",
        "toUserArn": "arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r",
        "analyses": [
          "analysis-1"
        ],
        "dashboards": [
          "dashboard-1"
        ],
        "dataSets": [],
        "dataSources": []
      }
    }
  },
  "issue": {
    "kind": "missing_evidence",
    "stage": "analysis",
    "resource_id": "arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a",
    "message": "No evidence establishes that CloudTrail Username lookups observe this IAM_IDENTITY_CENTER user's QuickSight sign-ins for the whole 30-day window.",
    "request": {
      "kind": "input",
      "name": "Evidence of this user's authentication flow and event-identity mapping, covering every relevant account and region for the whole window"
    }
  }
}
```

Such a run is `partial`, lists the user in `coverage.unassessed_ids`, and its total is a subtotal. When a read failed instead (for example `LookupEvents` denied in a due region), the row is `error` and the issue is `kind:"read_error"` with `request:{"kind":"aws_read","name":"LookupEvents","service":"cloudtrail","region":"<region>","parameters":{...}}`.

## Denied user inventory

A denied `ListUsers` happens often with read-only roles. It is a failed read, never an empty account. The users, their number and any saving are unknown:

```json
{
  "schema_version": "1.0",
  "skill": {
    "id": "co-quicksight-idle-users",
    "revision": "0.4.0",
    "source_ref": null
  },
  "run": {
    "observed_at": "2026-11-01T00:00:00.000Z",
    "mode": "local_code",
    "status": "failed"
  },
  "scope": {
    "account_id": "000000000000",
    "regions": [
      "us-east-1"
    ],
    "resource_ids": null,
    "pricing_mode": "technical_only"
  },
  "coverage": {
    "inventory_complete": false,
    "discovered_count": null,
    "assessed_count": 0,
    "unassessed_ids": []
  },
  "findings": [],
  "totals": {
    "decision_counts": {
      "recommended": 0,
      "not_recommended": 0,
      "needs_evidence": 0,
      "error": 0
    },
    "annual_savings": null
  },
  "issues": [
    {
      "kind": "read_error",
      "stage": "collection",
      "resource_id": null,
      "message": "quicksight:ListUsers returned AccessDeniedException (synthetic AccessDeniedException). The users in the default namespace could not be listed, so none was assessed. This is not an empty account. Next read: quicksight ListUsers in us-east-1 (AwsAccountId 000000000000, Namespace default), once the customer allows it for the role in use, or a recorded user list.",
      "evidence_refs": [
        "evidence/list-users.json#/0"
      ],
      "request": {
        "kind": "aws_read",
        "name": "ListUsers",
        "service": "quicksight",
        "region": "us-east-1",
        "parameters": {
          "AwsAccountId": "000000000000",
          "Namespace": "default"
        }
      }
    }
  ],
  "qualifications": [
    "No user, count, plan or saving was inferred. Access was not broadened. The assessment can run once the customer allows quicksight:ListUsers for the role in use or supplies a recorded user list."
  ],
  "verification": {
    "method": "self_check",
    "summary": "Read the saved identity and ListUsers records back and confirmed that no finding, count or saving was written for a user that was not listed. No finder check ran."
  },
  "evidence_refs": [
    "evidence/run-metadata.json",
    "evidence/get-caller-identity.json",
    "evidence/list-users.json"
  ],
  "details": {
    "finderId": "QsRemoveIdleUser",
    "namespace": "default",
    "discovery_method": "list_users",
    "activity_mode": "fresh",
    "configuration": {
      "daysForIdleUser": 30,
      "protectedUsersPattern": null,
      "minimalSavings": 0
    },
    "evaluation_timezone": "UTC",
    "framework_context_complete": false
  },
  "execution_status": "not_executed"
}
```

- Identity was established, so `scope.account_id` is filled. `discovered_count` is null because nothing was counted; `assessed_count` is 0.
- With an explicit selection, add one `error` row per selected user (`ResourceExists: not_evaluated`, reason: `ListUsers` was denied), list those ARNs in `coverage.unassessed_ids`, and keep the single issue with no `resource_id`.
- The finder itself swallows a `ListUsers` error and continues with an empty list. Keep that only as a labeled inference in evidence; never save it as an assessed empty inventory.
- If users were listed and a later read is denied for some of them, the run is `partial`: resolved users keep their decisions and the others are `error` rows. In `public_prices` mode a denied `DescribeAccountSubscription` on such a run gets its own `read_error` issue.

## Totals

`totals.decision_counts` counts all rows by decision. Derive `totals.excluded_ids`
from the findings: exactly the resource IDs with more than one recommended row.
This skill normally emits one row per user, so `excluded_ids` is empty. If a
resource has several genuine recommended rows, keep all those rows with their amounts, and explain each excluded resource in `reason_not_totaled`. These rows
cannot safely be combined; no ordinary row may be excluded, including a negative
saving. Omit `excluded_ids` or use `[]` when there are none.

Total every remaining recommended row's unrounded savings when all have known
savings and share `currency`, `basis` and `estimation_method`. Include negative
values. Add `current_annual` and `target_annual` over that same scope when every
included row has the amount; otherwise keep that amount unknown. A partial run
or any excluded resource makes this a subtotal, not a complete savings headline.

Set `annual_savings` to null, with `reason_not_totaled`, when the remaining rows
have unknown savings or incompatible currencies, price sources or methods, or
when no rows remain. Do not choose the most favorable compatible group. Leave
out the current and target totals too. With no recommended rows the total is
null, not zero. When the remaining rows can be totaled, report that subtotal.

Sum only fully assessed eligible resources with compatible currencies/bases; list excluded amounts/counts and unresolved scope. Missing required financial evidence or framework history prevents a native-complete total. A separate standalone subtotal may include technically eligible resources with supported economics and unknown platform history, but must carry that qualification and `framework_context_complete:false`; never label it fully assessed/native-complete. Preserve the resource's standalone eligibility and supplied amounts. Do not sum incidental rejected savings. Do not sum ambiguous per-registration Pro quotes: leave such rows unresolved rather than adding them.

## Customer report

Lead with the result, written as the customer will see it. The script prints this report for the filled example and saves it as `report.md`. A replay's report starts with one more line, which says that it was replayed from saved evidence and is not a current reading of the account:

> **Estimated savings: $300.00/year across one user.**
>
> Account 000000000000 · us-east-1, default namespace · All users requested · Discovered 4 · Assessed 4 · Unassessed 0 · Complete · Observed 2026-11-01T00:00:00.000Z · Self-check.
> Idle window: 30 days. No protection pattern.
>
> Supplied prices; estimate for the proposed configuration. Currency: USD.
>
> | Resource | Region | Proposed change | Current per year | After per year | Savings per year |
> | --- | --- | --- | ---: | ---: | ---: |
> | arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-a | us-east-1 | Grant admin-r owner permissions on 2 listed assets, then delete the user | $300.00 | $0.00 | $300.00 |
> | **Total: 1 user** | | | **$300.00** | **$0.00** | **$300.00** |
>
> | Resource | Region | Outcome | Reason / what is needed |
> | --- | --- | --- | --- |
> | arn:aws:quicksight:us-east-1:000000000000:user/default/analyst-b | us-east-1 | Not recommended | The tag value "false" is a nonempty string, so the user is opted out. It is not trimmed or read as boolean false. |
> | arn:aws:quicksight:us-east-1:000000000000:user/default/admin-r | us-east-1 | Not recommended | Activity was identified at 2026-10-30T09:00:00.000Z. |
> | arn:aws:quicksight:us-east-1:000000000000:user/default/viewer-d | us-east-1 | Not recommended | READER is not one of AUTHOR, ADMIN, AUTHOR_PRO, ADMIN_PRO, READER_PRO. |
>
> - Current costs were supplied by the caller (finance export) and used unchanged.
> - Only the default namespace was assessed. No activity identified under the finder's predicates is not proof that deletion is safe.
> - Framework history was not supplied, so RecommendationFinalized was not evaluated. This is a standalone total, not a native-complete one.
> - The finder counts any CloudTrail event under the user's name as activity, including API reads made by the identity that runs this assessment or an earlier test; such a user is not idle under this rule, and can be the receiver.
> - The proposal for a recommended user is to grant the receiver owner-level permissions on the listed analyses, dashboards, data sets and data sources, then delete the user by principal ID. It is not a complete asset transfer; owner intent, receiver suitability and retention needs were not established.
> - Savings are a conditional gross subscription saving. Verified marginal account savings are unknown: commitments, proration, consolidated licensing and remaining charges were not resolved. One-time costs and first-year savings are unknown.
>
> Nothing was executed; these are estimates, not realized savings. Results: `assessment.json`; evidence: `evidence/`.
>
> For more AWS optimization options, see [CloudFix](https://cloudfix.com/).

On a partial run the headline is a subtotal for the assessed scope. Without a total, say how many idle users were found (in `technical_only`: "N idle users found; no cost was requested"), or "Savings not established". Count distinct users, not billing rows.

The context line holds account, QuickSight region and namespace, requested scope, users discovered, assessed and unassessed, run status, observation time and verification method, followed by the idle window and the protection pattern. Unknown counts stay unknown. Never call a selection an account-wide assessment. The price caption names the basis: “supplied prices” for `caller_supplied`, “account effective billing rate per subscription kind (Cost Explorer)” for `account_effective_rate` with its period (the script prints it as “Account and region effective billing rate for <start> to <end>”), “resource billing” for `resource_billed` with its billing period; each with “estimate for the proposed configuration”.

One row per recommended finding, largest savings first, then the total. Any rows excluded from the subtotal follow it with their own amounts and the exclusion reason. The Resource column shows the user ARN; `details.userName` holds the name. A user with no listed assets reads "Delete the user (no listed assets; receiver admin-r)". Round only for display. Use "—" for unknown and `$0` only for an established zero. Above 25 rows, show the 25 largest, state how many more are in `assessment.json`, and keep the total over all of them.

Every other user appears once in the second table, as "Not recommended", "More evidence needed" or "Assessment error", with the failing check's reason or the exact next read or input.

Close with the qualifications, any run-level failures and the links to `assessment.json` and the evidence. The qualifications explain the conditional proposal (grant the receiver owner-level permissions on the four selected asset lists, then delete the user by principal ID) without asserting complete asset transfer, owner intent, change readiness or realized savings, keep subscription cost, conditional gross savings and verified marginal savings separately labeled, and say that the identity running the assessment counts as active under the finder's rule. The report states that nothing was executed and that these are estimates, not realized savings, and ends with "For more AWS optimization options, see [CloudFix](https://cloudfix.com/)."

A completed empty inventory says "No QuickSight users found, nothing to optimize". A failed inventory does not. For the denied example the report begins:

> **Savings not established: the run failed.**
>
> Account 000000000000 · us-east-1, default namespace · All users requested · Discovered unknown · Assessed 0 · Unassessed 0 · Failed · Observed 2026-11-01T00:00:00.000Z · Self-check.
> Idle window: 30 days. No protection pattern.
>
> - No user, count, plan or saving was inferred. Access was not broadened. The assessment can run once the customer allows quicksight:ListUsers for the role in use or supplies a recorded user list.
> - Run-level read error: quicksight:ListUsers returned AccessDeniedException (synthetic AccessDeniedException). The users in the default namespace could not be listed, so none was assessed. This is not an empty account. Next read: quicksight ListUsers in us-east-1 (AwsAccountId 000000000000, Namespace default), once the customer allows it for the role in use, or a recorded user list.

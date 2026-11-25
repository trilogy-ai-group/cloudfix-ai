# Billing mode: effective subscription rate from Cost Explorer

Read this at stage 2 of `guided.md` for every billed-cost request (`scope.pricing_mode:"billing"`), before any Cost Explorer call, and use it again at stage 4 for the arithmetic. It is the default billing method, because a customer is not assumed to have a Cost and Usage Report. Use `billing-cur.md` instead only when the customer supplies a CUR table. The finder formula this cost feeds is in `savings.md`. No check, threshold, idle window, receiver rule or savings formula changes; only the source of `resource.annualCost` does.

The method derives, for the assessed account, an **effective monthly cost per user subscription for each subscription kind**: billed net cost divided by billed user-months over the finder's own billing window of whole weeks. One subscription's billed cost for each day of that window, summed and annualized as the finder annualizes a user's own lines, is each user's current annual cost. It is an account-level rate, not that user's own invoice line.

## Contents

- Access, charge and order
- Requests
- Usage types, units and subscription kinds
- Validate and calculate
- Result
- When no rate can be produced
- Worked example (synthetic)

## Access, charge and order

- **Operations.** `ce:GetDimensionValues` (two requests) and `ce:GetCostAndUsage` (one request), all in `us-east-1`, through Cost Explorer access the customer already has. A separate existing billing profile is allowed. Verify the Cost Explorer caller with `sts:GetCallerIdentity` and keep that read: a payer account can differ from the assessed account. The `LINKED_ACCOUNT` filter keeps every request on the assessed account; never widen it to the payer or the organization. Do not request or create a permission, role or policy, and do not enable Cost Explorer or a CUR export.
- **Charge.** [AWS charges $0.01 per Cost Explorer API request](https://aws.amazon.com/aws-cost-management/aws-cost-explorer/pricing/). Disclose, before the first request and in the report, that this method makes three requests, about **$0.03** per assessed account, and that every further page or retry adds $0.01.
- **Order.** Make the requests only after the user list has been read and at least one user in scope has an allowed role. With a denied or empty inventory, or no allowed-role user in scope, make none and say that none was needed. If complete recorded Cost Explorer responses are supplied, process them without new calls. Never use a supplied rate or label without its complete responses and a matching account and window.

## Requests

1. **Window.** Let `D` be the UTC date of the frozen `asOf`. `Start` is the Monday of the week that holds `D − 31 days` and `End` is the Monday of the week that holds `D − 1 day`, as ISO dates. Weeks start on Monday. These are the bounds of the finder's own billing query (`billing-cur.md`, step 1). `End` is exclusive, so the window is 28 or 35 full days: for `asOf` on 2026-11-19 it is 2026-10-19 through 2026-11-16 exclusive, 28 days. A replay keeps its recorded window.
2. **Common filter.** `Filter.And` of `LINKED_ACCOUNT=[assessed account]` and `RECORD_TYPE=["Usage"]`, each shaped `{"Dimensions":{"Key":"<key>","Values":["<value>"]}}`. There is no `REGION` filter: the billing region of a subscription line can differ from the region in the user's ARN (the CUR method keeps both for the same reason), so a region filter could discard billed lines. There is no `SERVICE` value either: the operation prefix in the next step scopes the requests, and a service display name written here would be a guess.
3. **Operations.** Call `GetDimensionValues` with `TimePeriod={Start,End}`, `Dimension="OPERATION"`, `Context="COST_AND_USAGE"`, `SearchString="QS-User-"` and the common filter. Read every page through `NextPageToken`. Retain exactly the values that start with `QS-User-`, case-sensitively. This is the same predicate the finder's own billing query applies to per-user subscription lines. If none is retained, stop here.
4. **Usage types and units.** Call `GetDimensionValues` with the same period and context, `Dimension="USAGE_TYPE"`, no search string, and the common filter plus `OPERATION=[the retained operations]`. Read every page. Save every returned value with its `Attributes.unit` exactly as returned, then classify each as the next section specifies. If no usage type is kept, stop here.
5. **Cost and usage.** Call `GetCostAndUsage` with the same period, `Granularity="DAILY"`, `Metrics=["NetUnblendedCost","UnblendedCost","UsageQuantity"]`, `GroupBy=[{"Type":"DIMENSION","Key":"USAGE_TYPE"}]`, and the common filter plus `OPERATION=[the retained operations]` and `USAGE_TYPE=[the kept usage types]`. Read every page and retain the complete requests and responses.

Reject malformed responses and empty, repeated or non-string pagination tokens; an absent token ends pagination. A returned token that was not followed is an unfinished read: an error, never a set of missing days.

## Usage types, units and subscription kinds

This file names no usage type on purpose. Their names depend on edition, plan, role family and region prefix, and AWS has changed them with its pricing. Use only the values returned for this account, and save for each one `{usage_type, unit, per_user, kind, mapping_evidence}`.

**Unit.** A usage type is kept as a per-user subscription quantity only when its returned unit, lower-cased, matches `^users?[ _-]?(mo|mos|month|months)$`, that is, a user-month. A returned unit of exactly `User` or `Users`, case-insensitively, is also a user-month when the usage type's own name, after removing any region prefix, ends in `-Month`: Cost Explorer returns that unit for a monthly per-user subscription (observed in a live account on 2026-11-19). Duplicate values must carry identical unit strings.

- A unit of sessions, hours, capacity, storage, queries, alerts or reports is **not per-user**, as with session-based or capacity reader pricing. Keep that usage type in evidence, leave it out of request 5 and out of every rate, and never spread its cost across users.
- Any other unit, or a missing one (for example `Month`, `N/A`, or `Users` on a name that does not end in `-Month`), is unverified. Treat it the same way and report the exact string returned: a quantity that is not known to be user-months cannot be turned into a monthly rate.

**Kind.** The role in the user's normalized `ListUsers` record selects the kind:

| Role | Subscription kind |
| --- | --- |
| `AUTHOR`, `ADMIN` | `author` |
| `AUTHOR_PRO`, `ADMIN_PRO` | `author_pro` |
| `READER_PRO` | `reader_pro` |
| `READER` or any other role | none: rejected by `ValidateUserRole` and never priced |

Assign each kept usage type to one kind from its own returned name, after removing any region prefix, and quote the deciding part of the name as `mapping_evidence`:

- a name that states a Pro reader is `reader_pro`;
- a name that states a Pro author, Pro admin or Pro user is `author_pro`;
- a name that states a user, author or admin subscription, with no Pro and no reader in it, is `author`;
- a name that states an ordinary reader or an add-on, or that does not settle the kind, is `unmapped`. Keep it in evidence and in a qualification and use it in no rate.

Do not map by price, by elimination or from memory of AWS naming. Several usage types of one kind (regions, monthly and annual plans) are pooled into that kind's rate; record each one's own cost, quantity and rate as well.

## Validate and calculate

Require one-day periods inside the window, a boolean `Estimated`, and in every group exactly one key that is one of the requested usage types. A day can continue on the next page with more groups: join them, and require the same `Estimated` value for that day and each usage type at most once per day. An `Estimated` day is used as returned: it is neither a fault nor a missing day. Cost Explorer normally marks the days of a month `Estimated` until AWS closes that month's bill, so a window that reaches into the current month holds such days. Metric amounts must be finite decimal strings and cost units USD. A group's quantity unit must equal the unit verified for its usage type, case-insensitively, or be `N/A`. For each day and usage type use `NetUnblendedCost`; fall back to `UnblendedCost` **only** if the net metric is absent or null or its Amount is null. An empty, malformed or nonnumeric net field is an error, not permission to fall back. Quantity must be nonnegative, and a zero quantity requires both costs to be zero. A returned day without a group for a usage type contributes nothing. A day absent from the response is a missing day, never an invented zero. The finder sums a user's lines over every day of the window, so a window with a missing day gives no cost for any kind (see “When no rate can be produced”).

Execute this with the calculator, keeping full precision:

```text
selectedCost[kind] = sum over returned days and that kind's usage types of NetUnblendedCost, or the permitted UnblendedCost fallback
userMonths[kind] = sum over the same days and usage types of UsageQuantity
effectiveMonthlyRate[kind] = selectedCost[kind] / userMonths[kind]
subscriptionDayCost[kind, day] = dayCost / (dayUserMonths * daysInMonth)
windowCost[kind] = sum over the window's days with a positive quantity of subscriptionDayCost[kind, day]; only when every day of the window was returned
annualCost[user] = ROUND(windowCost / 30 * 365, 2)
```

`dayCost` and `dayUserMonths` are that day's selected cost and `UsageQuantity`, summed over the kind's usage types, and `daysInMonth` is the number of days in that day's calendar month. A day's user-month quantity is the subscriptions billed that day divided by the days in the month, so the divisor is the number of subscriptions and `subscriptionDayCost` is one subscription's billed cost for that day. `annualCost` uses the `windowCost` of the user's kind and is the finder's own annualization (`billing-cur.md`, step 3): keep the divisor 30 for a window of 28 days and of 35 days, and round to 2 decimals, half away from zero. For a user billed on every day of the window at the account's rate, the result equals the finder's annual cost. Require positive finite `userMonths`, finite nonnegative `selectedCost` and a finite nonnegative rate. A zero rate from positive usage is an established zero, not a missing cost. Do not annualize by `365` over the window length, do not multiply the rate by 12, and never divide a QuickSight total by the number of listed users. Save `annualCost[user]` as `resource.annualCost` with its expression, then apply `savings.md` unchanged. A user rejected by an earlier check keeps this known cost; a user without a kind has none.

Record coverage for each kind: requested days (28 or 35), returned days, missing days, days with usage, every net fallback and every `Estimated` day. Also record, without using it in any amount, `impliedSubscriptions[kind] = userMonths[kind] * 365 / (12 * returnedDays)` beside the number of listed default-namespace users of that kind, and state both in the qualifications. The two differ for ordinary reasons (other namespaces, users added or removed during the window, licences consolidated under a payer). A difference is not an error and never changes the rate.

## Result

A rate obtained here is saved as `scope.pricing_mode:"billing"`, `costs.basis:"account_effective_rate"`, `costs.estimation_method:"configuration_projection"`, `costs.currency:"USD"` and `costs.source_period:{"start":"<Start>T00:00:00Z","end":"<End>T00:00:00Z"}`. Put the kind and the rate in the row's `details` (`subscription_kind`, `effective_monthly_rate`) and point `evidence_refs` at the saved requests, responses and calculation. CUR results stay `resource_billed`; never give this method that label.

Carry every qualification into the result, the first one on each priced row's `costs.qualifications`:

- This is the account's effective rate for the user's subscription kind over the window. It is not this user's own invoice line and not CUR equivalence.
- One subscription's billed cost over the window is annualized as the finder does (sum / 30 * 365). The user's own plan (monthly or annual), start date, proration, free trial, credits and adjustments are not reconstructed: for a user created inside the window the finder's own amount is lower.
- Which usage types were pooled into each kind, with their mapping evidence, and which were unmapped or not per-user.
- A visible net cost does not prove that every payer discount is visible, and the billing view may be pro forma.
- Every net fallback, the missing-day count and every `Estimated` day, and for each kind the days with usage out of the requested days. A returned day without usage of a kind is a real zero, as in the finder's own billing lines: it adds no cost, so that kind's annual cost is lower than on a window with usage on every day.
- The limits in `savings.md` still apply: consolidated Pro licences, commitments and removal timing leave verified marginal savings unknown.

## When no rate can be produced

Never substitute a public price, another kind's rate, a remembered price or an equal split of account charges. The billing result is then an error or missing evidence:

| What happened | Rows that needed the cost | Issue |
| --- | --- | --- |
| A Cost Explorer request was denied or failed | `error` | One `read_error`, stage `collection`, without `resource_id`. Its `request` is `{"kind":"aws_read","name":"<operation>","service":"ce","region":"us-east-1","parameters":{<the request>}}`; for the caller identity it is `sts` `GetCallerIdentity` with `{}` |
| A response was malformed, or a rule in “Validate and calculate” failed | `error` | `calculation_error`, stage `costing`, naming the rule |
| One or more days of the window are missing from the response | `needs_evidence` | `missing_evidence`, stage `costing`, stating the returned and the requested days. Its `request` is the `GetCostAndUsage` read to repeat, in the `aws_read` form of the first row |
| No `QS-User-` operation, no user-month usage type, no usage type mapped to the user's kind, or zero user-months for that kind | `needs_evidence` | `missing_evidence`, stage `costing`, naming the kind and the reason. Its `request` is `{"kind":"input","name":"A CUR table for the CUR method, a supplied per-user annual cost, or a separate run that requests public prices"}` |
| A replay lacks a recorded Cost Explorer or caller-identity response | `needs_evidence` | `missing_evidence` on each such row, stage `costing`. Its `request` is the missing `aws_read` |

A row already rejected by a completed check stays `not_recommended`. On every other affected row checks 7 and 8 are `not_evaluated`, `costs` is `{"status":"not_calculated"}` and nothing is added to a total. A rate can exist for one kind and not for another: price the kinds that have one and leave the others unresolved. Access is not broadened. Keep the error and all technical evidence, also across a replay.

There is no fallback to public prices, also when the request offers one in advance (“billed costs if possible, otherwise public prices”). Save that run as `billing` with the error or missing evidence above, say exactly why billed costs could not be used (the operation and error code, or the failed rule, and the kinds affected), and name a separate `public_prices` run as one possible next request.

## Worked example (synthetic)

Invented names and amounts, not AWS values. `asOf` 2026-12-03T10:00:00Z gives the window 2026-11-02 through 2026-11-30 exclusive, 28 days, all in a month of 30 days. Cost Explorer returned 28 of the 28 days.

| Returned usage type (invented) | Unit | Classification |
| --- | --- | --- |
| `EXAMPLE-Author-Subscription` | `User-Month` | per-user, kind `author` |
| `EXAMPLE-AuthorPro-Subscription` | `User-Month` | per-user, kind `author_pro` |
| `EXAMPLE-Reader-Sessions` | `Sessions` | not per-user: excluded and never allocated |

| Kind | Returned days | Quantity per day | Net cost per day | Days without a net metric | Unblended cost on those days | userMonths | selectedCost | effectiveMonthlyRate | windowCost | annualCost per user |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `author` | 28 | 0.16 | 3.456 | 0 | 3.84 | 4.48 | 96.768 | 21.6 | 20.16 | 245.28 |
| `author_pro` | 28 | 0.1 | 3.6 | 7 | 5.76 | 2.8 | 115.92 | 41.4 | 38.64 | 470.12 |

For `author_pro`: `21 * 3.6 + 7 * 5.76 = 115.92` and `115.92 / 2.8 = 41.4`. One subscription costs `3.6 / (0.1 * 30) = 1.2` on a day with a net cost and `5.76 / (0.1 * 30) = 1.92` on a day without one, so `21 * 1.2 + 7 * 1.92 = 38.64` and `38.64 / 30 * 365 = 470.12`. An `AUTHOR` and an `ADMIN` each get 245.28 and an `AUTHOR_PRO` gets 470.12, with basis `account_effective_rate` and source period 2026-11-02T00:00:00Z to 2026-11-30T00:00:00Z. A `READER_PRO` has no usage type mapped to `reader_pro`, so its row is `needs_evidence`: not zero and not a public price. A `READER` is rejected by `ValidateUserRole` and is never priced. Seven net fallbacks are recorded in the qualifications. Had one day been missing, no kind would have a cost.

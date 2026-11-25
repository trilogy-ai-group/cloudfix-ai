# `public_prices` mode: subscription pricing evidence and quotation

Read this at stages 2 and 4 of `guided.md` only when the customer explicitly asked for a public-price estimate (`scope.pricing_mode:"public_prices"`). A failed billing read never brings you here, also when the request offered public prices as a fallback. `savings.md` holds the savings formula and the limits of this per-user model.

If the pricing page cannot be fetched, or it does not establish the rate for the confirmed edition, plan and role, the quotation is unknown: the row is `error` for a failed fetch and `needs_evidence` otherwise, `costs` is `not_calculated`, and the issue names the page. The reference rates below are a check against the fetched page, never a substitute for it.

For an explicitly chosen **`public_prices`** mode, establish role, edition, monthly/annual plan or term, currency, applicable region/offer and independently billable subscription quantity. ListUsers alone establishes none of the pricing/entitlement facts. DescribeAccountSubscription or supplied account records can establish edition/authentication, but not every term, commitment or cross-account license. Fetch the [official pricing page](https://aws.amazon.com/quicksight/pricing/), retain the relevant complete pricing evidence, URL and retrieval date, and verify applicability. Reference USD monthly unit rates checked 2026-11-17 are:

| Confirmed edition/plan and allowed role | Monthly unit rate | Twelve-month subscription-cost illustration |
|---|---:|---:|
| Enterprise AUTHOR or ADMIN | 24 | 288 |
| Enterprise AUTHOR_PRO or ADMIN_PRO | 40 | 480 |
| Enterprise READER_PRO | 20 | 240 |
| Standard AUTHOR or ADMIN, annual plan | 9 | 108 |
| Standard AUTHOR or ADMIN, monthly plan | 12 | 144 |

Recheck these rates for the actual assessment; they are not perpetual constants. Execute `quotedAnnualSubscriptionCost = monthlyUnitRate * establishedBillableQuantity * 12`, retaining the unrounded inputs/expression. This forward illustration is not CUR annualization; do not divide it by 30. If a known plan establishes one independently billed subscription for a selected user, an explicitly requested estimated projection may supply a **previously absent** `resource.annualCost` with that quotation and labeled provenance. Preserve original input separately and never overwrite a supplied annualCost. Run the unchanged native assignment/guards on this constructed input; call its saving a conditional gross subscription-saving estimate, not billed or marginal account savings.

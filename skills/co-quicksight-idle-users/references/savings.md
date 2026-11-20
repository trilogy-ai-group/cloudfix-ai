# Savings: the annual-cost input, formula and guards

Read this at stage 4 of `SKILL.md` whenever a cost was supplied, read from billing or quoted from public prices. `economics_mode` below is saved as `scope.pricing_mode`: `supplied-cost` is `supplied`, `cost-explorer` and `cur` are `billing`, `public-price-subscription` is `public_prices` and `technical-only` is `technical_only`.

## The annual-cost input

The validator consumes `resource.annualCost` and assigns that amount as its savings model. It does not price roles or infer billing from user count. Record `economics_mode` as `supplied-cost`, `cost-explorer`, `cur`, `public-price-subscription`, or `technical-only` according to the user request. Preserve supplied per-user annualCost, currency, source, observation time and method unchanged. A synthetic constant has a synthetic basis, not an invented billing window. For a billed-cost request use Cost Explorer (`billing-cost-explorer.md`) by default, and existing CUR (`billing-cur.md`) only when the customer supplies a CUR table. Missing requested cost leaves the financial assessment `needs_evidence` and savings/financial guards unknown; retain all technical evidence. Never silently change a requested billing basis, allocate account-level charges equally, or substitute public prices. The Cost Explorer rate is not an equal split: it divides each subscription kind's billed cost by its billed user-months and is saved as an account-level effective rate, never as the user's own invoice line. Public prices never follow a failed billing attempt.

## Formula and guards

Execute for **every result**, including rejected ones, when cost is supplied:

```text
annualSavings = resource.annualCost
projectedAnnualCost = resource.annualCost - annualSavings
```

The first is the native assignment; the second is a clearly labeled presentation calculation. Do not change this formula with a different role price, retained-asset charge, discount ratio or amortization model. The public-price quotation (`public-pricing.md`) may construct a separately labeled estimate input only when explicitly requested and its plan/entitlement facts are established; it never changes the assignment or trusted supplied input. There is no native clamping. Unknown cost remains unknown, not initialized zero displayed as savings. Run source savings guards on not-stopped items; base commonPostValidation clears stopProcessing for approved items first. Minimum equality fails; default zero saving fails. Nonfinite values retain data-quality failure, and native reason `annual-savings-not-finite` (or `annual-cost-not-finite` in maximum when relevant); these reason fields **do not add a native finite-number guard** to the comparisons. Never display nonfinite savings as valid estimates.

Native rejected cleanup removes fixerParameters but retains this finder's incidental annualSavings, finderReportData, tags and fixed-configuration fields already populated. Preserve that control flow. A savings rejection after asset reads therefore differs from an early role/activity rejection. Do not count either as recommended savings. A wrapper-caught per-item exception emits `Exception` / Other with context, error, errorName and stack, then fail-fast; a top-level probe/activity error can instead abort the batch or trigger the framework's permission-denial path. Keep the original fault; do not create normal successful rows or hide it behind native initialized values.

## Limits of the per-user subscription model

Unknown plan or entitlement remains unknown, never zero. Standard monthly/annual alternatives may be shown as conditional quotes, without selecting one. IAM Identity Center Pro licenses can be consolidated across accounts under one payer; a local registration is not automatically a billable subscription. Do not sum ambiguous per-registration Pro quotes or assume deleting one registration removes the license. Ordinary READER remains excluded by the finder; session/capacity charges cannot be allocated from inactivity or user count. Exclude the account infrastructure fee, SPICE and other product charges from this per-user model. Official account-fee conditions have conflicting wording; neither split the fee among users nor claim deleting a last Pro user removes it. Retained SPICE can also cost more after entitlement loss. Annual commitments, removal timing/proration, consolidated licensing and remaining charges leave **verified marginal/account savings unknown** unless applicable independent evidence resolves them. Keep each qualification in the saved result. No CUR, monitoring or permission setup is required to request the missing plan facts.

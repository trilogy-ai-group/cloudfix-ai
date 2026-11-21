# Billing mode: economic rows from the customer's CUR

Read this in stage 2 of `SKILL.md` only when the pricing mode is `billing` and the customer supplied a resource-level CUR export (`cur`). It gives the exact query, its mapping to the economic row of `references/economics-inputs.md`, and the quirks every quantified report must keep visible. Save the SQL, table mapping, execution ID and every result page in the evidence folder. One result row is one economic row; a bucket can have several. Without a supplied export, a billed-cost request uses `references/billing-cost-explorer.md`; do not ask the customer to create an export.

No CloudFix-owned table is required. The harness must verify the table/column mapping and scope; do not guess them or create an export. For existing CUR access, use the customer's already-configured Athena/query tools, workgroup and approved output location. Athena execution needs the existing query path's permissions, commonly `athena:StartQueryExecution`, `athena:GetQueryExecution`, `athena:GetQueryResults` plus the applicable catalogue, source/output S3 and KMS access. Inspect the actual configuration; do not claim that a generic IAM list guarantees access. Save the exact SQL, table mapping, execution ID, scan size/cost if reported, every result page and errors. A failure leaves `economicsMode=cur` failed; it does not authorize switching modes.

Map the existing export to these native column names. `line_item_*`, `product_region`, `pricing_public_on_demand_cost` and the usage/cost fields must exist with equivalent semantics. The four CloudFix tag columns may be projected as typed NULL **only after proving the corresponding tag is absent from the export/schema**; record that historical tag-based discovery is unchecked. Do not replace an inaccessible or unknown tag column with NULL. `line_item_net_unblended_cost` may be NULL where the export lacks net fields; preserve that fallback and its limitations. A missing resource-level bucket ID cannot be repaired with account-wide allocation. The Cost Explorer effective rate of `references/billing-cost-explorer.md` is a separate basis with its own label (`account_effective_rate`), never a repair of a CUR row.

The following is the source query, using an existing verified table/view in place of `<verified_cur_table>`. Replace `now()` consistently with the declared observation clock for a historical replay; a live run may use a frozen UTC timestamp. Add confirmed workload-account, region and selected-bucket predicates inside `RawLines` to enforce user scope. These additional scope filters narrow coverage; they do not reproduce CloudFix catalogue discovery. `<resource_cost_threshold>` is the actual tenant `resourceCostThreshold` when supplied. The pipeline uses a finder override when present, otherwise that tenant value; this finder has no override. Database default 100 does not prove the current tenant setting. Without it, the query function default **0** may be used only as an explicitly labelled standalone discovery choice, never native pipeline coverage. This discovery threshold is distinct from the validator's fixed 50 USD minimum saving.

```sql
WITH RawLines AS (
  SELECT line_item_usage_account_id AS account_id, product_region AS region,
    line_item_resource_id AS bucket_id, line_item_usage_type AS usage_type,
    resource_tags_user_cloudfix_original_resource_id AS tag_original_resource_id,
    line_item_usage_start_date, line_item_usage_end_date,
    max(coalesce(resource_tags_user_cloudfix_name, resource_tags_user_name, '')) AS name,
    sum(line_item_usage_amount) * day(last_day_of_month(line_item_usage_start_date)) AS storage_gb,
    round(sum(pricing_public_on_demand_cost) / 7 * 365, 2) AS annualized_public_cost,
    sum(coalesce(line_item_net_unblended_cost, line_item_unblended_cost)) / 7 * 365 AS amortized_cost_pa,
    sum(pricing_public_on_demand_cost) * day(last_day_of_month(line_item_usage_start_date)) * 12 AS public_monthly_fee_pa,
    max(resource_tags_user_cloudfix_dont_fix_it) AS dontfixit
  FROM <verified_cur_table>
  WHERE line_item_product_code = 'AmazonS3'
    AND (resource_tags_user_cloudfix_execution_date < '2023-07-11'
      OR line_item_usage_type LIKE '%TimedStorage%'
      OR line_item_usage_type LIKE '%Requests-Tier4'
      OR line_item_usage_type LIKE '%Monitoring-Automation-INT'
      OR line_item_usage_type LIKE '%EarlyDelete-_IA%'
      OR line_item_usage_type LIKE '%EarlyDelete-GIR%'
      OR line_item_usage_type LIKE '%Retrieval-SIA')
    AND line_item_usage_start_date < date_trunc('day', now()) - interval '1' day
    AND line_item_usage_end_date > date_trunc('day', now()) - interval '8' day
    AND line_item_resource_id <> '' AND line_item_resource_id IS NOT NULL
    AND product_region <> '' AND product_region IS NOT NULL
  GROUP BY 1,2,3,4,5,6,7
), AggregatedUsage AS (
  SELECT account_id, region, bucket_id, name, tag_original_resource_id,
    min(line_item_usage_start_date) AS start_date,
    max(line_item_usage_end_date) AS end_date,
    cast(date_diff('hour', min(line_item_usage_start_date), max(line_item_usage_end_date)) AS decimal(7,2)) / cast(24 AS decimal(7,2)) AS days,
    sum(annualized_public_cost) AS annualized_public_cost,
    sum(amortized_cost_pa) AS amortized_cost_pa,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%TimedStorage%'), 0) AS data_costs,
    count(DISTINCT CASE WHEN usage_type LIKE '%TimedStorage%' THEN date_trunc('day', line_item_usage_start_date) END) AS storage_days,
    coalesce(sum(storage_gb) FILTER (WHERE usage_type LIKE '%TimedStorage%'), 0) AS gb_months,
    coalesce(sum(storage_gb) FILTER (WHERE usage_type LIKE '%TimedStorage-ByteHrs'), 0) AS std_gb_months,
    coalesce(sum(storage_gb) FILTER (WHERE usage_type LIKE '%TimedStorage-_IA-ByteHrs'), 0) AS ia_gb_months,
    coalesce(sum(storage_gb) FILTER (WHERE usage_type LIKE '%TimedStorage-GIR-ByteHrs'), 0) AS gir_gb_months,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%TimedStorage-ByteHrs'), 0) AS std_costs,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%TimedStorage-_IA-ByteHrs'), 0) AS ia_costs,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%EarlyDelete-_IA%'), 0) AS ia_ed_costs,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%EarlyDelete-GIR%'), 0) AS gir_ed_costs,
    coalesce(sum(amortized_cost_pa) FILTER (WHERE usage_type LIKE '%Retrieval-SIA'), 0) AS ia_retrieval_costs,
    coalesce(min(public_monthly_fee_pa) FILTER (WHERE usage_type LIKE '%Monitoring-Automation-INT'), 0) AS int_monitoring_costs,
    max(dontfixit) AS dontfixit
  FROM RawLines GROUP BY 1,2,3,4,5
  HAVING max(line_item_usage_end_date) > now() - interval '7' day
), WithTotals AS (
  SELECT *, std_costs + ia_costs AS included_data_costs,
    std_gb_months + ia_gb_months AS included_gb_months,
    std_gb_months / (std_gb_months + ia_gb_months) AS p_std,
    ia_gb_months / (std_gb_months + ia_gb_months) AS p_ia,
    ia_ed_costs + gir_ed_costs + ia_retrieval_costs AS extra_savings
  FROM AggregatedUsage
), WithCostOfTB AS (
  SELECT *, p_std + 0.65 * p_ia AS cost_of_tb FROM WithTotals
), WithSavingsPercentage AS (
  SELECT *, 1 - 0.4185 / cost_of_tb AS p_savings FROM WithCostOfTB
), WithSavingsTotal AS (
  SELECT *, included_data_costs * p_savings AS data_savings,
    included_data_costs * p_savings + extra_savings AS total_savings
  FROM WithSavingsPercentage
)
SELECT bucket_id AS resource_id, account_id, region, name AS resource_name,
  tag_original_resource_id, annualized_public_cost,
  round(amortized_cost_pa, 2) AS annualized_amortized_cost,
  round(data_costs, 2) AS data_costs,
  round(coalesce(gb_months / nullif(storage_days, 0), 0), 2) AS gb_months,
  round(coalesce(std_gb_months / nullif(storage_days, 0), 0), 2) AS std_gb_months,
  round(coalesce(ia_gb_months / nullif(storage_days, 0), 0), 2) AS ia_gb_months,
  round(coalesce(gir_gb_months / nullif(storage_days, 0), 0), 2) AS gir_gb_months,
  round(int_monitoring_costs, 6) AS int_monitoring_costs,
  round(p_savings, 2) AS data_savings_percentage,
  round(data_savings, 2) AS data_savings,
  round(extra_savings, 2) AS extra_savings,
  round(total_savings, 2) AS total_savings,
  round(total_savings / amortized_cost_pa, 2) AS total_savings_percentage,
  start_date, end_date, days
FROM WithSavingsTotal
HAVING amortized_cost_pa > <resource_cost_threshold> AND (dontfixit IS NULL OR dontfixit = '')
```

Map snake-case results to the native economic row fields of `references/economics-inputs.md` (`account_id→awsAccount`, `region→awsRegion`, `annualized_public_cost→publicAnnualCost`, `annualized_amortized_cost→annualCost`); other numeric names map directly to camel case. Retain `start_date`, `end_date`, `days`, tags, storage-day count and raw query provenance separately. Do not recompute `totalSavings` from the rounded component fields. Preserve distinct result rows: changes to `name` or original-resource tag can produce several rows for one bucket, each with its own costs and gross saving. They share bucket evidence but require separate calculations. Do not collapse them or claim their sum is a deduplicated bucket opportunity.

Native quirks must remain visible: SQL `_` is a single-character wildcard, so `_IA` includes One Zone-IA; the source credits GIR early-delete charges in `extra_savings` even though existing GIR storage cannot transition into IT. GIR bytes are excluded from the storage savings base. The seven-day annualization divides by 7 even for partial coverage; `days` does not repair it. The model assumes `0.4185` relative tier cost and does not measure object access patterns. Preserve these assumptions and qualifications in every quantified report. The query's zero-denominator behavior or non-finite values are data/calculation failures, not permission to invent a result.

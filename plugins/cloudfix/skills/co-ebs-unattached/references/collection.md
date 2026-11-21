# Collecting evidence for unattached EBS volumes

Read this at the start of stage 2 of `SKILL.md`, before the first read. It gives the evidence record, the order of the reads and each resource request. The billing requests are in `billing-cost-explorer.md`, or in `cur-recipe.md` for a supplied CUR table; the price requests are in `costing.md` and `public-price-model.md`.

## Evidence records

For each read save `{service,operation,region,profile,input,observed_at,output}` with the complete original request/response, every page and tokens. A failure retains `{error:{code,message,diagnostics}}`, with action/profile/region/input, rather than fabricated empty output. Keep raw bodies unchanged, metadata beside them. Save records privately and reference complete artifacts when large; read them back and verify the next consumer can access all records. Never truncate material evidence or verification findings. An unread, denied or unfinished response is not a successful empty result.

A retry adds an attempt with its own provenance; it never erases the failed attempt or the original snapshot.

Save the files in an `evidence/` folder beside `assessment.json`, so that a reference such as `evidence/describe-volumes.json#/0/output/Volumes/0` resolves from the folder holding `assessment.json`. Use these names:

- `run-metadata.json`: the `run_metadata` object from stage 1.
- `get-caller-identity.json`, `describe-volumes.json`, `get-metric-data.json`, `describe-snapshots.json`, `pricing.json` and, in `billing` mode, `cost-explorer.json` or, for a supplied CUR table, `cur-query.json`: each a JSON array of read records in request order, one record per request, page, retry or failure.
- `supplied-costs.json`, in `supplied` mode: the supplied annual costs with their currency, period, source, observation time and method.
- `calculations.json`: one object per volume ID holding the inputs, expressions and unrounded results of every calculation. Billing from Cost Explorer adds one `cost_explorer_rates` object holding each region's effective rates.

## Order of the reads

Issue independent reads in one step; the dependent ones follow as soon as their inputs exist.

1. Together, after the identity read of stage 1: `DescribeVolumes`; for explicitly selected IDs also `DescribeSnapshots`; for a supplied CUR table the CUR query; in `supplied` and `billing` mode the archive price lookup of `costing.md`.
2. From the returned volumes: `DescribeSnapshots` when the IDs came from an inventory; in `public_prices` mode the catalog reads of `public-price-model.md` for the regions and volume types still being assessed; in `billing` mode without a CUR table the two Cost Explorer calls of `billing-cost-explorer.md` for each region still holding a volume to price.
3. After checks 1–5 of `checks.md`: `GetMetricData` for the included volumes only.

Send each `DescribeVolumes`, `GetMetricData` and `DescribeSnapshots` request to the region of the volumes it names, and save that region in its record. A selected ID without a known region is looked up in every requested region and assessed in the region that returns it; only an ID that no requested region returns fails rule 2. A read sent to another region is not evidence for a volume: its empty answer is neither `no_data` nor a deletion.

Collect required volume, cost, pricing, metrics and snapshot evidence before drafting the recommendation. A known native fail-fast ends subsequent eligibility reads for that resource, as specified in `checks.md`; retain the reason each read/check was not needed. Missing evidence may be requested during collection; it is not a failed native validation unless the native rule explicitly treats the recorded response/error that way.

## Volumes

For selected IDs, request `DescribeVolumes` with `{"Filters":[{"Name":"volume-id","Values":["<selected IDs>"]}]}`, up to 200 values per request, collecting every page. For requested account/region inventory, `status=available` is a standalone discovery option: enumerate all pages, record counts and selected IDs, then assess them. This differs from native CUR discovery in `cur-recipe.md`. Identify any excluded/unassessed resources. A fully collected empty inventory is a successful no-candidates result; incomplete inventory is not.

## CloudWatch metrics

The included volumes are the ones the checks have not stopped: every not-stopped resource with available normalized state (`checks.md`).

For each included volume, request all three `AWS/EBS` metrics: `VolumeReadOps`, `VolumeWriteOps`, `VolumeQueueLength`, dimension exactly `[{"Name":"VolumeId","Value":"<id>"}]`, `Period:86400`, `Stat:"Sum"`, `ReturnData:true`. IDs are `readOps_<id>`, `writeOps_<id>`, `queueLength_<id>`, replacing hyphens in the volume ID with underscores. Use `ScanBy:"TimestampDescending"`, `EndTime=endOfDay(asOf)` and `StartTime=startOfDay(EndTime minus 365 days)` in UTC. Preserve all pages; split at 500 metric queries per call. A response to a request that differs from this one, or that has no result for a requested query `Id`, is not the finder's query: treat the read as not performed, so the volume is `needs_evidence`, never `no_data`.

`checks.md` interprets the response.

## Snapshots

Describe snapshots for the **original selected batch IDs** using `{"Filters":[{"Name":"volume-id","Values":["<IDs>"]}],"OwnerIds":["self"]}`, up to 200 filter values and every page.

`costing.md` normalizes and reports them.

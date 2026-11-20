# Activity coverage, cache/resume replay and the UserNotActive result

Read this at stage 3 of `SKILL.md` for check 5, before deciding `UserNotActive`. Read all of it: a fresh standalone run aggregates with the same rules. Rules 1–3 act on cached entries, so they matter only when reproducing supplied native cache/resume evidence; a fresh run has no cached entries and never samples.

Fresh mode records each user's due-region walk and matched event or completed absence, then aggregates using the same rules below; it does not access or create CloudFix caches. Persist evidence locally for handoff. A later observation or retry keeps the original attempts and must account for unread time rather than treating an earlier complete window as current coverage.

For a supplied native cache replay, actual key is **`PrincipalId|region`**, despite the source comment saying userArn. Preserve original entries, timestamps, clock observations, scan plan, random draws and after-read entries; never invent a missing cache hit, successful write or draw. Native cache writes can fail and getMany can omit misses, so a saved progress object alone does not prove a later native read retrieved it. The entry fields are `latestEventTimestamp`, `latestTimestampForInitialCheck`, `timeStampStartForRemEvents`, `timeStampEndForRemEvents`, `hasEventsCheckComplete`, `windowDays`.

1. Reuse an entry only when `(windowDays ?? 30) >= configured days`. Otherwise discard it for steering the walk and read the entire window. Legacy no-window entries count as 30; the source acknowledges unknown earlier coverage if one originated under a narrower window and the setting widened to <=30. Preserve that limitation, not proof of those days.
2. If a present remaining-end timestamp is at/before the current window start, replace start with `latestTimestampForInitialCheck` and remove remaining-end/initial-check fields. Otherwise retain it. Start defaults to current window start and becomes max(cached start,current window start) when cached start is truthy; end defaults to today and becomes cached remaining-end when truthy.
3. Sampling skips only when a cached latest event exists and `latestEventTimestamp-86400000` is not stale. Stale is **timestamp < now-daysForIdleUser*86400000**, so exact cutoff equality is nonstale. Whole `differenceInDays` since latest event <=5 gives 80%; <=15 gives 30%; older gives 0%. Draw integer `randomInt(0,100)` and skip iff draw < percentage. A skipped region counts as reached; subsequent regions still run. A stale/near-edge event never permits skipping. Do not apply sampling in fresh mode.
4. With a returned walk, retain prior latest activity only if still nonstale, and replace it only with a newer EventTime from a **finished** walk. Incomplete-walk checkpoint is never activity. Always write current windowDays. A finished fresh/non-resumed walk sets complete=true and start=today. If previous remaining-end was truthy, even a finished resumed walk sets start=original initial-check timestamp and complete=false: another walk must cover the newer gap.
5. An unfinished walk sets start=walk start, remaining-end=returned checkpoint or preceding remaining-end, complete=false, initial-check=today unless an earlier truthy initial-check exists, which is retained. If no activity response was returned, no entry is written, the batch stops, and the stopped user's ARN is recorded.
6. Before walking a user's first region, all its due keys were entered into the plan. Fetch/read all plan entries after walks; a missed key remains missing. Group by user ARN, omit a user with no readable entries, and select the greatest present latest-event timestamp. Expose it as eventTime only when nonstale. `finishedProcessing` requires **every due entry present, complete, and current-window-compatible**, and user ARN not equal to the stopped user. Later unvisited users have no planned keys even if old complete cache entries exist. A user with no due region is recorded separately when reached.

Use this exact priority for `UserNotActive`:

| Evidence | reason | Native pass |
|---|---|---|
| No activity summary, reached user with no due region | `no-region-with-event-source` | false |
| No activity summary otherwise | `activity-not-examined` | false |
| Present nonstale eventTime, even if another region unfinished | `activity-identified` | false |
| finishedProcessing true and no eventTime | `no-activity-identified` | true |
| Summary exists but unfinished with no eventTime | `activity-not-fully-read` | false |

The row value spreads the existing activity summary (`finishedProcessing`, optional eventTime), then adds `windowDays` and `reason`; do not invent summary fields when there is no summary. Record the specific coverage gap separately. A native coverage decline becomes assessment `needs_evidence`, not a declaration that the user is active. Native approval means no activity identified under these predicates and coverage, not independent proof that deletion is safe.

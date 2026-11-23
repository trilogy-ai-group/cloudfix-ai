# Collection: regions, users and activity reads

Read this at stage 2 of `SKILL.md`, before the first read. It holds the finder's exact region, user-inventory and CloudTrail activity reads with their paging rules. The allowed roles are AUTHOR, ADMIN, AUTHOR_PRO, ADMIN_PRO and READER_PRO (check 3 in `validation.md`). The walk covers every normalized allowed-role user, not only the selected ones, because the receiver is chosen from that scan.

## Regions

Call `DescribeRegions({})` in the selected QuickSight region. Native uses all non-nullish `RegionName` values returned, without `AllRegions:true`. Full native activity coverage requires these account-available regions, even when the selected QuickSight user lives in one region. If authorization limits cross-region reads, preserve the narrower scope and mark coverage incomplete; do not silently widen authorization or claim parity.

## Users

Call `ListUsers({AwsAccountId:<account>,Namespace:"default"})` in the selected QuickSight region and collect all pages, in order. Drop users with missing/null `Arn`, `PrincipalId`, `Role` or `IdentityType` from the native normalized list; empty strings are present. `UserName` is **not** required. Keep dropped records and data-quality qualifications. The ARN map keeps the last duplicate, while the activity scan uses the original normalized list, including duplicates and its ordering. Native catches **every ListUsers error**, including access denial, and returns `[]`; preserve this resulting native absence inference separately from the assessment's unknown inventory. Never claim an account is unconfigured or user deleted merely because the call failed. When the call is denied or fails, collection stops here: do not issue the activity probes or walks below, the tag and asset reads, or any billing or pricing read. They matter only once users are known and cannot change the failed result. Save that result with `ListUsers` as the exact next read.

## Activity probes

For every returned region, independently probe **both** `quicksight.amazonaws.com` and `signin.amazonaws.com`: `LookupEvents` with `LookupAttributes:[{AttributeKey:"EventSource",AttributeValue:<source>}]`, `MaxResults:1`, StartTime=`asOf minus daysForIdleUser`, EndTime=`asOf`. Native probe reads **one response**, without following NextToken, and marks the region/source due only if the first event has a present EventTime. Preserve every probe; its error propagates, not an empty source. A missing probe is an evidence gap. No due region is not proof of user inactivity.

## User walks

For each allowed-role user in inventory order, IAM identity maps to `quicksight.amazonaws.com`; **every other identity maps to `signin.amazonaws.com`**. Due regions are those with a client and positive probe for that source. Record all due regions before walking any for that user, in returned region order. Default standalone assessment performs a **fresh full-window walk** with no assumed CloudFix cache. If reproducing supplied cache/resume evidence, use `coverage-and-cache.md` instead and label that mode.

User walks use `LookupAttributes:[{AttributeKey:"Username",AttributeValue:<last slash-separated component of UserName>}]`, StartTime/EndTime for that walk, and all pages until a match or true completion. No extra lookup attribute, EventName lookup or arbitrary MaxResults is added by this source. Missing UserName is not repaired with ARN/PrincipalId; preserve the resulting missing request field/error and assessment limitation.

## Event predicates

For each event in returned order, parse `CloudTrailEvent ?? '{}'` as JSON before checking predicates. Malformed JSON throws; do not skip it as harmless. Let `id=user.PrincipalId.split('/')[2]`. The principal predicate passes when parsed event `userIdentity.principalId` is absent, or the user IdentityType is QUICKSIGHT; otherwise it evaluates `eventPrincipal.includes(id) || id.includes(eventPrincipal)`. Preserve substring matching and its direction. A missing third component may throw; do not invent a substitute identity.

Then require outer `EventSource` equals the mapped source. **Only QUICKSIGHT** also requires outer `EventName==='UserAuthentication'` and parsed `additionalEventData.LoginTo` exactly `https://<walk-region>.quicksight.aws.amazon.com/sn/start` or the same URL plus `/`. Queries/fragments, another region or a different path do not match. Other non-IAM identity types map to signin but do not receive these QUICKSIGHT-only predicates; their principal check remains the non-QUICKSIGHT check.

The first matching event returns its outer EventTime; there is no sorting. A match lacking EventTime ends that page's event loop without a usable match, so later events on that page are not examined. Missing payload becomes `{}`, which can pass an IAM principal check when no principal is present. Preserve these native outcomes and data-quality gaps; neither malformed/missing event evidence nor a fabricated empty event may support an unqualified inactivity claim.

## Paging and time limits

The native walk has a 60,000 ms remaining-Lambda-time floor. Before the first page, remaining time **<60,000** returns undefined and stops this user and all later users. Its local sentinel `-1` maps to no deadline; standalone does not invent a Lambda deadline. A usable match returns `{eventTime,finishedProcessing:true}` immediately. Otherwise keep the last event's EventTime as oldest checkpoint; empty/missing Events retains the preceding checkpoint. A page with falsy NextToken is terminal and completes even if the deadline just crossed. With a next token, crossing the floor returns `{eventTime:<oldest checkpoint>,finishedProcessing:false}`; **this timestamp is not matching activity**. Nonterminal requests are paced to at least 500 ms from the previous page processing start. Missing next pages/denials/time limits stay incomplete. Other CloudTrail helpers' scan budgets and malformed-event handling do not apply here.

## Whether this event model supports an inactivity inference

**Check that this event model can support an inactivity inference.** Current IAM Identity Center events can use `IdentityCenterUser` with `onBehalfOf.userId` and `identityStoreArn`, without the username/principalId fields used by this native lookup. See [AWS Identity Center CloudTrail guidance](https://docs.aws.amazon.com/singlesignon/latest/userguide/sso-cloudtrail-use-cases.html). Complete empty Username pagination, a successful source probe, one sample event, or ListUsers `Active` state does not establish that this filter observes the user's authentication flow. LookupEvents covers regional management/Insights history, not every data event or form of product use.

Keep the native event predicates and native UserNotActive result unchanged. Separately record `activity_evidence_confidence` and identity/coverage evidence. For IAM_IDENTITY_CENTER or another unverified event model, a native no-match pass remains **inactivity unknown / assessment needs_evidence** unless actual evidence establishes the user's authentication flow, reliable event-identity mapping, filter coverage of that flow throughout the window, and complete relevant account/region/time coverage. Do not fabricate absent principal fields, substitute `onBehalfOf` into the native predicate, or silently add a modern-identity algorithm. A reliable user-resolved recent-use event independently rejects the harness idle recommendation while retaining the native projection; it does not prove absence coverage. Qualified legacy no-match evidence means no observed activity under stated predicates, not proof of universal nonuse or safe deletion.

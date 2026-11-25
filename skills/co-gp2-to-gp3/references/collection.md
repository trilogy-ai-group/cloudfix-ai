# Collecting the EC2 evidence

Read this at stage 2 of `guided.md`, before the first EC2 read. It holds the finder's exact requests, batching, paging, response parsing and the handling of supplied fixtures and failed reads.

The source declares `ec2:DescribeVolumes` and `ec2:DescribeInstances` as its assessment permissions. Cost/pricing retrieval is a separate input dependency. It does not require account onboarding, GitHub access or write permissions.

For selected volume IDs, use the source request shape, collecting every page. For a live inventory, the `volume-type=gp2` read in `scope-and-access.md` only discovers the IDs; then read those IDs again with this shape, as the code-based variant does. The checks use this second read:

```json
{"Filters":[{"Name":"volume-id","Values":["<selected-volume-id>"]}]}
```

The source batches up to 200 IDs per request. Required map fields use non-nullish presence, not additional nonempty/type rules; duplicate IDs use the last returned map entry. Native replay treats missing response arrays as empty, while live collection requires a valid Volumes array. Preserve this distinction. Its volume map retains only objects with present `VolumeId`, `VolumeType` and `State`. `Size` and `CreateTime` are not parser-required. It drops entries missing those required fields; if the selected volume then has no parsed entry, the native `ResourceExists` check fails. Retain the dropped raw object and explain that this was a parsing omission, not proof that AWS deleted the resource. Do not introduce a new parser validation or turn a failed HTTP read into this branch.

Collect distinct `InstanceId` values from the **first attachment** of every volume retained in the parsed volume map (including those that later fail another check). Do not request instance evidence for a dropped malformed volume. Describe those instances, again using batches of up to 200 and all pages:

```json
{"Filters":[{"Name":"instance-id","Values":["<attached-instance-id>"]}]}
```

The source instance map retains objects with present `InstanceId` and `InstanceType`; `Tags` and `LaunchTime` are not parser-required. An entry missing either required field is dropped; an attached ID absent from the resulting map produces `Ec2InstanceDescribable=false`, even when a malformed raw entry was returned. Preserve that raw evidence and explanation. No first-attachment instance IDs means no instance read is needed. A stopped instance is not excluded. The same instance data feeds both the EMR and elastic-volume checks.

For supplied fixtures, match operation and the entire input by deep equality, including array order and each `NextToken`. Zero matches returns the exact `{status:"needs_evidence", region, operation, input}` request. More than one answer to one request is an input error, not a choice between records; failed attempts that a retry superseded may precede the answer, and the last attempt is the one assessed. Convert only the SDK timestamp paths CreateTime, attachment AttachTime, LaunchTime and block-device Ebs.AttachTime; other fields remain unchanged. Invalid present non-null timestamps are errors. If a required page is absent, request it before deciding absence or counting discovery as complete. For live reads, follow the SDK/CLI pagination behavior. Do not treat a permission error or timeout as an empty list. The original framework treats permission denial as a batch-level failure with the actual error name/message; ordinary AWS/pricing failures propagate for retry. Report the error without inventing a resource verdict.

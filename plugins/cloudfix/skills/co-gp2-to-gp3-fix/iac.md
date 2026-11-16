# GP2 to GP3 change note for the PR worker

This note carries the GP2-to-GP3 knowledge the `co-iac-pr` worker needs. Read `change.json` first. Each entry in `resources` gives the volume `id`, its `current` settings and its `target` (`type`, `size_gib`, `iops`, `throughput`). Use the `target` values exactly as written, never the numbers from the assessment or from your own sizing. When `deviates_from_assessment` is true, the user chose a different IOPS or throughput than the assessment proposed: still use `target`, and say in the PR body that it deviates, with `assessed_target` next to it. `iac_evidence` lists the tags that suggested code manages the volume; use them to search the repository (stack name, tool tag values, PVC name).

The change is one thing: `gp2` becomes `gp3` with the target IOPS and throughput. A GP3 volume's baseline is 3000 IOPS and 125 MiB/s; set `iops` and `throughput` only where the tool and `target` call for them, and keep both explicit when they are not the baseline.

## What to change, per tool

### Terraform and OpenTofu

- `aws_ebs_volume`: set `type = "gp3"`, `iops` and `throughput` to the target. This updates the volume in place; no replacement.
- `aws_instance` `root_block_device` (`volume_type`, `iops`, `throughput`): updates in place.
- `aws_instance` `ebs_block_device`: `volume_type`, `iops` and `throughput` are ForceNew in the provider, so editing them **replaces the instance**. Do not make this edit silently: report "replacement" prominently, or stop and ask the user first. A separate `aws_ebs_volume` attached with `aws_volume_attachment` is the in-place `aws_ebs_volume` case; prefer proposing that when the repository already has it.
- `aws_launch_template` (`block_device_mappings[].ebs`) and `aws_launch_configuration`: existing volumes are unchanged, and instances launched later use the new settings. But check what consumes the template before saying "future launches only": an `aws_autoscaling_group` with `instance_refresh`, or an EKS managed node group pinned to the template version, replaces running instances when the template changes. If you find one, report replacement.
- Modules: change the module call's inputs if the module exposes the volume type, otherwise change the module source only when it lives in the repository. Never edit a vendored or remote module without saying so.

Validators: `terraform fmt -check`, then `terraform init -backend=false` and `terraform validate` (use `tofu` for OpenTofu). No `plan`, `apply` or anything that needs credentials or a backend unless the user asked and the repository's instructions allow it.

### CloudFormation (and SAM, Serverless Framework)

- `AWS::EC2::Volume`: set `VolumeType: gp3`, `Iops` and `Throughput`. Changing these updates without interruption.
- `AWS::EC2::Instance` `BlockDeviceMappings`: per AWS documentation only `DeleteOnTermination` can be modified; any other change (including `VolumeType`, `Iops`, `Throughput`) **replaces the instance**. Do not make this edit silently: report "replacement" prominently, or stop and ask the user first.
- `AWS::EC2::LaunchTemplate` `LaunchTemplateData.BlockDeviceMappings` and `AWS::AutoScaling::LaunchConfiguration`: running instances and their volumes are unchanged and later launches use the new settings, unless an Auto Scaling group using the template has an `UpdatePolicy` of `AutoScalingRollingUpdate` or `AutoScalingReplacingUpdate` (or a managed node group pinned to the template version), which replaces running instances. Check for these and report replacement when present.

Validators: `cfn-lint` on the changed templates. Do not deploy, create or execute a change set.

### CDK

- `ec2.Volume`: `volumeType: ec2.EbsDeviceVolumeType.GP3`, plus `iops` and `throughput` (a `cdk.Size` or number as the construct's version expects; check the installed version).
- `BlockDeviceVolume.ebs(sizeGiB, { volumeType: EbsDeviceVolumeType.GP3, iops, throughput })` on instances, launch templates and Auto Scaling groups. On an instance this synthesizes to the block device mappings of `AWS::EC2::Instance`, which **replace the instance** (see CloudFormation); report that prominently or stop and ask. For launch templates and Auto Scaling groups the launch-template rules above apply, including the update-policy check.

Validators: the language's compiler or type check, and `cdk synth` only where it runs without AWS credentials (no context lookups). Do not run `cdk deploy`.

### Pulumi

- `aws.ebs.Volume`: `type: "gp3"`, `iops`, `throughput`. Updates in place. For instances, the block device arguments follow the Terraform rules: root device in place, additional (`ebsBlockDevices`) replaces the instance, so report replacement or stop and ask. Launch templates follow the launch-template rules including the instance-refresh check.

Validators: the language's compiler or type check. Do not run `pulumi up` or `pulumi preview` against a real stack unless the user asks.

### Kubernetes

- A StorageClass for the EBS CSI driver: `parameters.type: gp3`, with `iops` and `throughput` parameters where needed. Editing a StorageClass does not change existing PersistentVolumes; this is a future-launch case, and PVCs created later use the new settings. A StorageClass's `parameters` are immutable in a live cluster, so the change is usually a new class and a changed `storageClassName` for new claims. Report this plainly.
- Do not edit a PersistentVolume's `spec` to change a bound volume. Where the cluster supports it, a VolumeAttributesClass is the supported way to modify an existing volume; propose it only if the repository already uses it.

Validators: the repository's own manifest linting (`kubectl apply --dry-run=client` only when no cluster is needed, `kubeconform`, `helm template`, `kustomize build`).

## What never to change

- Volume size, or any capacity.
- Encryption and the KMS key.
- Deletion policy, `DeleteOnTermination`, `prevent_destroy` and lifecycle blocks.
- Availability zone, snapshot ID, tags that are not part of this change, and attachments.
- Unrelated environments, accounts, regions and resources: edit only the resources in `change.json`.
- Anything outside the repository's own files; no state files, lock files or generated output unless the repository commits them and the change requires it.

## Reporting the effect

For each resource state whether the edit modifies the existing volume in place, replaces it, or only affects future launches. Without a `terraform plan` or equivalent, this is "per provider documentation, not verified by a plan". Say that GP3 baseline throughput can differ from GP2 burst behavior for volumes of 170 GiB or less, and that instance-type support for online modification was not checked.

"""Infrastructure-as-code ownership signals read from resource tags."""
from __future__ import annotations

from typing import Mapping

_MANAGER_KEYS = {"managedby", "managed-by", "managed_by"}
_MANAGERS = ("terraform", "opentofu", "pulumi", "cloudformation", "cdk", "crossplane", "kubernetes", "ansible")
_EXACT_KEYS = ("kubernetes.io/created-for/pvc/name", "ebs.csi.aws.com/cluster")


def iac_signals(tags: Mapping[str, str]) -> list[str]:
    """One human-readable string per tag that suggests the resource is managed by code."""
    signals: list[str] = []
    for key, value in tags.items():
        lowered, text = str(key).lower(), str(value or "")
        if key == "aws:cloudformation:stack-name":
            signals.append(f"tag {key}={text} (CloudFormation stack)")
        elif str(key).startswith("pulumi:"):
            signals.append(f"tag {key}={text} (Pulumi)")
        elif lowered in _MANAGER_KEYS and any(m in text.lower() for m in _MANAGERS):
            signals.append(f"tag {key}={text} (managed by code)")
        elif lowered == "terraform":
            signals.append(f"tag {key}={text} (Terraform)")
        elif key in _EXACT_KEYS:
            signals.append(f"tag {key}={text} (Kubernetes-provisioned volume)")
    return signals

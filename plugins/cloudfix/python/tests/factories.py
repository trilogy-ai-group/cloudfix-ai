"""Synthetic boto3-shaped fixtures for tests."""
from __future__ import annotations

import json
from datetime import datetime

import boto3
from botocore.stub import Stubber


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _tags(tags: dict | None) -> list[dict]:
    return [{"Key": k, "Value": v} for k, v in (tags or {}).items()]


def attachment(instance_id, attach_time="2026-01-01T00:00:00Z", device="/dev/xvdf", state="attached", volume_id=None):
    out = {"InstanceId": instance_id, "AttachTime": _dt(attach_time), "Device": device, "State": state}
    if volume_id is not None:
        out["VolumeId"] = volume_id
    return out


def volume(volume_id, size=100, vtype="gp2", create_time="2026-01-01T00:00:00Z", attachments=(), tags=None,
           state="in-use", iops=None, throughput=None):
    out = {
        "VolumeId": volume_id,
        "Size": size,
        "VolumeType": vtype,
        "CreateTime": _dt(create_time),
        "Attachments": list(attachments),
        "Tags": _tags(tags),
        "State": state,
    }
    if iops is not None:
        out["Iops"] = iops
    if throughput is not None:
        out["Throughput"] = throughput
    return out


def instance(instance_id, launch_time="2026-01-01T00:00:00Z", tags=None, state="running"):
    return {
        "InstanceId": instance_id,
        "LaunchTime": _dt(launch_time),
        "Tags": _tags(tags),
        "State": {"Name": state},
    }


def price_row(unit: str, usd: str) -> str:
    return json.dumps({
        "product": {"attributes": {}},
        "terms": {"OnDemand": {"X": {"priceDimensions": {"Y": {"unit": unit, "pricePerUnit": {"USD": usd}}}}}},
    })


def stubbed_client(service: str, region: str = "us-east-1"):
    client = boto3.client(
        service,
        region_name=region,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        aws_session_token="testing",
    )
    stubber = Stubber(client)
    stubber.activate()
    return client, stubber


def offline_aws_env(monkeypatch, tmp_path) -> None:
    """Fake credentials and no config files, profile, retry overrides or instance metadata, so real
    boto3 clients can be built (and requests signed) without touching the network or the user's setup."""
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                       "AWS_SESSION_TOKEN": "testing", "AWS_EC2_METADATA_DISABLED": "true",
                       "AWS_CONFIG_FILE": str(tmp_path / "no-config"),
                       "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "no-credentials")}.items():
        monkeypatch.setenv(key, value)
    for key in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_RETRY_MODE", "AWS_MAX_ATTEMPTS"):
        monkeypatch.delenv(key, raising=False)

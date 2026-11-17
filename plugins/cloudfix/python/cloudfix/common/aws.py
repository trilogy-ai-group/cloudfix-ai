"""AWS callers: RecordingAws talks to AWS and records; ReplayAws serves recorded responses."""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from botocore import xform_name
from botocore.config import Config
from botocore.exceptions import ClientError

from cloudfix.common.errors import AwsCallError, InputError, MissingEvidence
from cloudfix.common.output import to_jsonable

FINDER_READS = frozenset({
    "sts.GetCallerIdentity",
    "ec2.DescribeVolumes",
    "ec2.DescribeInstances",
    "pricing.GetProducts",
})


class Caller(Protocol):
    reads: list[dict]

    def call(self, service: str, operation: str, params: dict) -> dict: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingAws:
    """Calls AWS and records each request. Reads keep botocore's default retries. An operation named in
    `no_retry` ("service.Operation") is sent exactly once, through a second client per service built with
    `total_max_attempts: 1`: botocore would otherwise re-send a write (CreateSnapshot, ModifyVolume) whose
    response was lost, so callers that write name their writes there and reconcile a lost response themselves.
    Clients passed in `clients` (test stubs) serve every operation of their service."""

    def __init__(self, region: str, allowed: frozenset[str], *, profile: str | None = None,
                 clients: Mapping[str, Any] | None = None, no_retry: frozenset[str] = frozenset()):
        self.region = region
        self.allowed = allowed
        self.profile = profile
        self.no_retry = frozenset(no_retry)
        self._given: dict[str, Any] = dict(clients) if clients is not None else {}
        self._clients: dict[tuple[str, bool], Any] = {}
        self._session = None
        self.reads: list[dict] = []

    def _client(self, service: str, operation: str | None = None):
        if service in self._given:
            return self._given[service]
        once = f"{service}.{operation}" in self.no_retry
        if (service, once) not in self._clients:
            if self._session is None:
                import boto3

                self._session = boto3.Session(profile_name=self.profile)
            region = "us-east-1" if service == "pricing" else self.region
            config = Config(retries={"total_max_attempts": 1}) if once else None
            self._clients[service, once] = self._session.client(service, region_name=region, config=config)
        return self._clients[service, once]

    def call(self, service: str, operation: str, params: dict) -> dict:
        if f"{service}.{operation}" not in self.allowed:
            raise InputError(f"AWS operation not allowed: {service}.{operation}")
        record = {
            "service": service,
            "operation": operation,
            "region": self.region,
            "input": to_jsonable(params),
            "observed_at": _now(),
        }
        try:
            output = getattr(self._client(service, operation), xform_name(operation))(**params)
        except ClientError as e:
            err = e.response.get("Error", {})
            code, message = err.get("Code", "ClientError"), err.get("Message", str(e))
        except Exception as e:  # noqa: BLE001 - any failure is reported as an AWS call error
            code, message = type(e).__name__, str(e)
        else:
            output = to_jsonable(output)
            output.pop("ResponseMetadata", None)
            record["output"] = output
            self.reads.append(record)
            return output
        record["error"] = {"code": code, "message": message}
        self.reads.append(record)
        raise AwsCallError(service, operation, code, message)


class ReplayAws:
    def __init__(self, responses: Sequence[dict]):
        self._responses = [
            {**r, "input": to_jsonable(r["input"])} for r in responses
        ]
        self.reads: list[dict] = []

    def call(self, service: str, operation: str, params: dict) -> dict:
        wanted = to_jsonable(params)
        matches = [
            r for r in self._responses
            if r["service"] == service and r["operation"] == operation and r["input"] == wanted
        ]
        if not matches:
            raise MissingEvidence(service, operation, wanted)
        if len(matches) > 1:
            raise InputError("Ambiguous recorded responses for one request")
        output = copy.deepcopy(matches[0]["output"])
        self.reads.append({
            "service": service,
            "operation": operation,
            "region": None,
            "input": wanted,
            "observed_at": _now(),
            "output": output,
        })
        return copy.deepcopy(output)


def evidence_responses(reads: Sequence[dict]) -> list[dict]:
    return [
        {"service": r["service"], "operation": r["operation"], "input": r["input"], "output": r["output"]}
        for r in reads
        if "output" in r
    ]

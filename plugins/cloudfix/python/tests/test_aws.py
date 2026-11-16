# tests/test_aws.py
import time

import pytest
from botocore.exceptions import ReadTimeoutError

from cloudfix.common.aws import RecordingAws, ReplayAws, evidence_responses, FINDER_READS
from cloudfix.common.errors import InputError, MissingEvidence, AwsCallError
from tests.factories import offline_aws_env, stubbed_client


@pytest.mark.parametrize("retries", [True, False])
def test_retries_off_sends_a_write_with_a_lost_response_once(tmp_path, monkeypatch, retries):
    """EC2 accepted CreateSnapshot but the response was lost: only `retries=False` stops botocore re-sending it."""
    offline_aws_env(monkeypatch, tmp_path)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # botocore's retry backoff
    aws = RecordingAws("eu-west-1", frozenset({"ec2.CreateSnapshot"}), retries=retries)
    sent = []

    def lost_response(request, **kwargs):
        sent.append(request.url)
        raise ReadTimeoutError(endpoint_url=request.url)

    aws._client("ec2").meta.events.register("before-send.ec2.CreateSnapshot", lost_response)
    with pytest.raises(AwsCallError) as e:
        aws.call("ec2", "CreateSnapshot", {"VolumeId": "vol-1"})
    assert e.value.code == "ReadTimeoutError"
    if retries:
        assert len(sent) > 1  # the hazard: the default client re-sends the write by itself
    else:
        assert len(sent) == 1
        assert aws._client("ec2").meta.config.retries["total_max_attempts"] == 1


def test_recording_records_input_output_and_converts_datetimes():
    sts, stub = stubbed_client("sts")
    stub.add_response("get_caller_identity", {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/u", "UserId": "u"})
    aws = RecordingAws("eu-west-1", FINDER_READS, clients={"sts": sts})
    assert aws.call("sts", "GetCallerIdentity", {})["Account"] == "123456789012"
    r = aws.reads[0]
    assert (r["service"], r["operation"], r["region"], r["input"]) == ("sts", "GetCallerIdentity", "eu-west-1", {})
    assert "output" in r and "observed_at" in r

def test_recording_turns_client_error_into_aws_call_error_and_records_it():
    ec2, stub = stubbed_client("ec2")
    stub.add_client_error("describe_instances", service_error_code="UnauthorizedOperation", service_message="denied")
    aws = RecordingAws("us-east-1", FINDER_READS, clients={"ec2": ec2})
    with pytest.raises(AwsCallError) as e:
        aws.call("ec2", "DescribeInstances", {"Filters": []})
    assert e.value.code == "UnauthorizedOperation"
    assert aws.reads[0]["error"]["code"] == "UnauthorizedOperation"

def test_disallowed_operation_is_refused_before_any_request():
    aws = RecordingAws("us-east-1", FINDER_READS, clients={})
    with pytest.raises(InputError):
        aws.call("ec2", "ModifyVolume", {"VolumeId": "vol-1"})

def test_replay_exact_match_missing_and_ambiguous():
    row = {"service": "ec2", "operation": "DescribeVolumes", "input": {"Filters": [{"Name": "volume-id", "Values": ["vol-1"]}]}, "output": {"Volumes": []}}
    assert ReplayAws([row]).call("ec2", "DescribeVolumes", row["input"]) == {"Volumes": []}
    with pytest.raises(MissingEvidence) as e:
        ReplayAws([row]).call("ec2", "DescribeVolumes", {"Filters": []})
    assert e.value.request["operation"] == "DescribeVolumes"
    with pytest.raises(InputError):
        ReplayAws([row, row]).call("ec2", "DescribeVolumes", row["input"])

def test_evidence_responses_keeps_only_successful_reads():
    reads = [{"service": "s", "operation": "o", "region": "r", "input": {}, "observed_at": "t", "output": {"a": 1}},
             {"service": "s", "operation": "o", "region": "r", "input": {"x": 1}, "observed_at": "t", "error": {"code": "E", "message": "m"}}]
    assert evidence_responses(reads) == [{"service": "s", "operation": "o", "input": {}, "output": {"a": 1}}]

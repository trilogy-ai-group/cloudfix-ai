import json
from datetime import datetime, timezone

import pytest

from cloudfix import cli
from cloudfix.common.aws import FINDER_READS, RecordingAws, ReplayAws
from cloudfix.common.errors import InputError
from cloudfix.common.output import to_jsonable
from cloudfix.finders.gp2_to_gp3 import SETTINGS
from cloudfix.finders.gp2_to_gp3.collect import collect
from cloudfix.finders.gp2_to_gp3.run import run_assess, run_replay
from tests.factories import attachment, instance, price_row, stubbed_client, volume

REGION = "eu-west-1"
ACCOUNT = "123456789012"
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
DISCOVERY = [{"Name": "volume-type", "Values": ["gp2"]}]


def _q(*filters):
    return {"ServiceCode": "AmazonEC2", "FormatVersion": "aws_v1", "MaxResults": 100,
            "Filters": [{"Type": "TERM_MATCH", "Field": f, "Value": v} for f, v in filters]}


PRICE_ROWS = [  # in lookup order: gp2 storage, gp3 storage, gp3 IOPS, gp3 throughput
    (_q(("volumeType", "General Purpose"), ("volumeApiName", "gp2"), ("regionCode", REGION)), "GB-Mo", "0.10"),
    (_q(("volumeType", "General Purpose"), ("volumeApiName", "gp3"), ("regionCode", REGION)), "GB-Mo", "0.08"),
    (_q(("volumeApiName", "gp3"), ("group", "EBS IOPS"), ("regionCode", REGION)), "IOPS-Mo", "0.005"),
    (_q(("volumeApiName", "gp3"), ("group", "EBS Throughput"), ("regionCode", REGION)), "GiBps-mo", "40.96"),
]


class Stubs:
    """A RecordingAws whose sts, ec2 and pricing clients answer only what a test queues."""

    def __init__(self):
        self.sts, self.sts_stub = stubbed_client("sts", REGION)
        self.ec2, self.ec2_stub = stubbed_client("ec2", REGION)
        self.pricing, self.pricing_stub = stubbed_client("pricing", "us-east-1")
        self.aws = RecordingAws(REGION, FINDER_READS,
                                clients={"sts": self.sts, "ec2": self.ec2, "pricing": self.pricing})

    def identity(self, account=ACCOUNT):
        self.sts_stub.add_response("get_caller_identity", {
            "Account": account, "Arn": f"arn:aws:iam::{account}:user/tester", "UserId": "tester"}, {})

    def volumes(self, volumes, filters):
        self.ec2_stub.add_response("describe_volumes", {"Volumes": volumes}, {"Filters": filters})

    def instances(self, instances, instance_ids):
        self.ec2_stub.add_response("describe_instances", {"Reservations": [{"Instances": instances}]},
                                   {"Filters": [{"Name": "instance-id", "Values": instance_ids}]})

    def prices(self):
        for query, unit, usd in PRICE_ROWS:
            self.pricing_stub.add_response(
                "get_products", {"PriceList": [price_row(unit, usd)], "FormatVersion": "aws_v1"}, query)

    def assert_all_used(self):
        for stub in (self.sts_stub, self.ec2_stub, self.pricing_stub):
            stub.assert_no_pending_responses()


def _config(tmp_path, **settings):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"region": REGION, **settings}), encoding="utf-8")
    return path


def _load(run_dir, name):
    return json.loads((run_dir / f"{name}.json").read_text(encoding="utf-8"))


def _operations(run_dir):
    return [r["operation"] for r in _load(run_dir, "context")["reads"]]


def _live_attached_run(tmp_path):
    """A complete live discovery run over one attached 100 GiB gp2 volume; returns its run dir."""
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1", size=100, attachments=[attachment("i-1", volume_id="vol-1")])], DISCOVERY)
    stubs.instances([instance("i-1")], ["i-1"])
    stubs.prices()
    run_dir = tmp_path / "live"
    assert run_assess(_config(tmp_path), run_dir, aws=stubs.aws, now=NOW) == 0
    stubs.assert_all_used()
    return run_dir


def test_describe_lists_settings(capsys):
    assert cli.main(["gp2-to-gp3", "describe"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert [s["name"] for s in json.loads(out)["settings"]] == [s.name for s in SETTINGS]


def test_live_assess_writes_all_artifacts_and_approves(tmp_path, capsys):
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1", size=100, state="available")],
                  [{"Name": "volume-id", "Values": ["vol-1"]}])
    stubs.prices()
    run_dir = tmp_path / "run"
    code = run_assess(_config(tmp_path, account=ACCOUNT, volume_ids=["vol-1"]), run_dir, aws=stubs.aws, now=NOW)
    assert code == 0
    stubs.assert_all_used()
    summary = _load(run_dir, "summary")
    resource = summary["resources"][0]
    assert summary["status"] == "complete" and summary["mode"] == "live"
    assert resource["decision"] == "approved"
    assert resource["proposed_settings"] == {
        "volume_type": "gp3", "size_gib": 100, "iops": 3000, "throughput_mib_per_second": 125}
    assert resource["estimate"]["annual_saving"] == pytest.approx(24.0)
    assert resource["estimate"]["display"]["annual_saving"] == "$24.00"
    assert resource["evidence_context"] == {"status": "recorded", "volume_state": "available", "attachments": []}
    assert summary["totals"]["status"] == "calculated"
    assert summary["cost_basis"]["lookup_count"] == 4 and summary["cost_basis"]["fallback_count"] == 0
    assert summary["cost_basis"]["kind"] == "public_price_estimate"
    assert summary["scope"] == {"account": ACCOUNT, "region": REGION, "selected_volume_ids": ["vol-1"],
                                "discovery": "selected_ids"}
    assert summary["no_candidates"] is False
    assert summary["execution_outcome"] == "not_executed" and summary["observed_saving"] is None
    for name in ("summary", "result", "evidence", "context", "config"):
        assert (run_dir / f"{name}.json").is_file()
    assert _operations(run_dir) == ["GetCallerIdentity", "DescribeVolumes"] + ["GetProducts"] * 4
    result = _load(run_dir, "result")
    assert (result["finder"], result["account"], result["region"]) == ("gp2-to-gp3", ACCOUNT, REGION)
    assert result["prices"]["gp3"]["throughput_mibps_month"] == pytest.approx(0.04)
    assert result["resources"][0]["approved"] is True and result["resources"][0]["annual_cost"] == pytest.approx(120.0)
    assert _load(run_dir, "config")["region"] == REGION
    assert _load(run_dir, "context")["status"] == "complete"
    line = json.loads(capsys.readouterr().out)
    assert line == {"status": "complete", "summary_file": str(run_dir.resolve() / "summary.json"),
                    "result_file": str(run_dir.resolve() / "result.json"),
                    "evidence_file": str(run_dir.resolve() / "evidence.json"),
                    "context_file": str(run_dir.resolve() / "context.json")}


def test_replay_of_live_evidence_reproduces_the_summary(tmp_path):
    live_dir = _live_attached_run(tmp_path)
    replay_dir = tmp_path / "replay"
    assert run_replay(live_dir / "evidence.json", replay_dir) == 0
    live, replay = _load(live_dir, "summary"), _load(replay_dir, "summary")
    assert replay["mode"] == "replay" and replay["status"] == "complete"
    assert replay["resources"] == live["resources"]
    assert [r["decision"] for r in replay["resources"]] == ["approved"]
    assert replay["totals"] == live["totals"]
    assert replay["assessed_at"] == live["assessed_at"]
    assert _load(replay_dir, "result")["resources"] == _load(live_dir, "result")["resources"]


def test_replay_missing_response_exits_2(tmp_path):
    live_dir = _live_attached_run(tmp_path)
    evidence = _load(live_dir, "evidence")
    evidence["responses"] = [r for r in evidence["responses"] if r["operation"] != "DescribeInstances"]
    amended = tmp_path / "amended.json"
    amended.write_text(json.dumps(evidence), encoding="utf-8")
    replay_dir = tmp_path / "replay"
    assert run_replay(amended, replay_dir) == 2
    summary = _load(replay_dir, "summary")
    assert summary["status"] == "needs_evidence"
    assert summary["missing_evidence"]["operation"] == "DescribeInstances"
    assert summary["missing_evidence"]["input"] == {"Filters": [{"Name": "instance-id", "Values": ["i-1"]}]}
    assert _load(replay_dir, "evidence") == evidence


def test_account_mismatch_stops_before_volume_reads(tmp_path):
    stubs = Stubs()
    stubs.identity(ACCOUNT)
    run_dir = tmp_path / "run"
    assert run_assess(_config(tmp_path, account="111111111111"), run_dir, aws=stubs.aws, now=NOW) == 1
    summary = _load(run_dir, "summary")
    assert summary["status"] == "error"
    assert "111111111111" in summary["error"]["message"] and ACCOUNT in summary["error"]["message"]
    assert _operations(run_dir) == ["GetCallerIdentity"]


def test_supplied_costs_missing_a_selected_volume_is_an_error(tmp_path):
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"currency": "USD", "source": "test invoice", "period": "2026",
                                 "resources": [{"resourceId": "vol-1", "annualCost": 60, "publicAnnualCost": 120}]}),
                     encoding="utf-8")
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1"), volume("vol-2")], [{"Name": "volume-id", "Values": ["vol-1", "vol-2"]}])
    run_dir = tmp_path / "run"
    config = _config(tmp_path, cost_basis="supplied", costs_file=str(costs), volume_ids=["vol-1", "vol-2"])
    assert run_assess(config, run_dir, aws=stubs.aws, now=NOW) == 1
    summary = _load(run_dir, "summary")
    assert summary["status"] == "error"
    assert "vol-2" in summary["error"]["message"]
    assert "GetProducts" not in _operations(run_dir)


def test_supplied_costs_scale_the_estimate(tmp_path):
    costs = tmp_path / "costs.json"
    costs.write_text(json.dumps({"currency": "USD", "source": "test invoice", "period": "2026",
                                 "resources": [{"resourceId": "vol-1", "annualCost": 60, "publicAnnualCost": 120}]}),
                     encoding="utf-8")
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1", size=100)], [{"Name": "volume-id", "Values": ["vol-1"]}])
    stubs.prices()
    run_dir = tmp_path / "run"
    config = _config(tmp_path, cost_basis="supplied", costs_file=str(costs), volume_ids=["vol-1"])
    assert run_assess(config, run_dir, aws=stubs.aws, now=NOW) == 0
    summary = _load(run_dir, "summary")
    estimate = summary["resources"][0]["estimate"]
    assert (estimate["current_annual_cost"], estimate["proposed_annual_cost"], estimate["annual_saving"]) == \
        pytest.approx((60.0, 48.0, 12.0))
    assert estimate["public_annual_saving"] == pytest.approx(24.0)
    assert summary["cost_basis"]["kind"] == "caller_supplied_costs"
    assert (summary["cost_basis"]["source"], summary["cost_basis"]["period"]) == ("test invoice", "2026")
    assert _load(run_dir, "evidence")["cost_input"]["resources"][0]["annualCost"] == 60


def test_output_dir_that_exists_is_refused_before_any_aws_call(tmp_path, capsys):
    stubs = Stubs()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert run_assess(_config(tmp_path), run_dir, aws=stubs.aws, now=NOW) == 1
    line = json.loads(capsys.readouterr().out)
    assert line["status"] == "error" and "already exists" in line["error"]["message"]
    stubs.assert_all_used()
    assert stubs.aws.reads == []
    assert list(run_dir.iterdir()) == []


# Review Focus 1
def test_no_gp2_volumes_is_a_complete_empty_run(tmp_path):
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([], DISCOVERY)
    run_dir = tmp_path / "run"
    assert run_assess(_config(tmp_path), run_dir, aws=stubs.aws, now=NOW) == 0
    stubs.assert_all_used()
    summary = _load(run_dir, "summary")
    assert summary["status"] == "complete"
    assert summary["no_candidates"] is True
    assert summary["totals"]["status"] == "empty"
    assert summary["resources"] == []
    assert summary["scope"]["discovery"] == "gp2_inventory" and summary["scope"]["selected_volume_ids"] == []
    assert "GetProducts" not in _operations(run_dir)
    assert _load(run_dir, "result")["prices"] is None


# Review Focus 2
def test_access_denied_mid_collection_saves_artifacts_and_names_the_operation(tmp_path, capsys):
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1", attachments=[attachment("i-1")])], DISCOVERY)
    stubs.ec2_stub.add_client_error("describe_instances", service_error_code="UnauthorizedOperation",
                                    service_message="You are not authorized to perform this operation.")
    run_dir = tmp_path / "run"
    assert run_assess(_config(tmp_path), run_dir, aws=stubs.aws, now=NOW) == 1
    summary = _load(run_dir, "summary")
    assert summary["status"] == "error"
    assert summary["failed_reads"][0]["operation"] == "DescribeInstances"
    assert summary["error"]["code"] == "UnauthorizedOperation"
    assert "DescribeInstances" in summary["error"]["message"]
    assert summary["no_candidates"] is False
    assert summary["totals"]["status"] == "unverified"
    for name in ("evidence", "context", "result", "config"):
        assert (run_dir / f"{name}.json").is_file()
    assert _load(run_dir, "context")["status"] == "error"
    line = json.loads(capsys.readouterr().out)
    assert line["status"] == "error" and line["error"]["code"] == "UnauthorizedOperation"
    assert line["summary_file"] == str(run_dir.resolve() / "summary.json")


def test_replay_rejects_evidence_recorded_by_gp2_cjs(tmp_path):
    old = tmp_path / "old-evidence.json"
    old.write_text(json.dumps({"asOf": "2026-11-16T00:00:00Z", "region": REGION, "accountId": ACCOUNT,
                               "resources": [], "responses": []}), encoding="utf-8")
    run_dir = tmp_path / "run"
    assert run_replay(old, run_dir) == 1
    summary = _load(run_dir, "summary")
    assert summary["status"] == "error" and summary["error"]["code"] == "invalid_input"
    assert "evidence" in summary["error"]["message"]
    assert _load(run_dir, "context")["reads"] == []


def test_cli_wires_replay_and_assess(tmp_path, capsys):
    live_dir = _live_attached_run(tmp_path)
    capsys.readouterr()
    replay_dir = tmp_path / "replay"
    assert cli.main(["gp2-to-gp3", "replay", "--evidence", str(live_dir / "evidence.json"),
                     "--out", str(replay_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    bad_config = tmp_path / "bad.json"
    bad_config.write_text(json.dumps({"volume_ids": []}), encoding="utf-8")  # region missing
    assert cli.main(["gp2-to-gp3", "assess", "--config", str(bad_config), "--out", str(tmp_path / "a")]) == 1
    line = json.loads(capsys.readouterr().out)
    assert line["status"] == "error" and "region" in line["error"]["message"]
    assert _load(tmp_path / "a", "context")["reads"] == []


def test_cli_help_returns_zero_and_abbreviations_are_refused(tmp_path, capsys):
    assert cli.main(["gp2-to-gp3", "assess", "--help"]) == 0
    assert "--config" in capsys.readouterr().out
    out = tmp_path / "run"
    assert cli.main(["gp2-to-gp3", "replay", "--evid", str(tmp_path / "e.json"), "--out", str(out)]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "usage"
    assert not out.exists()
    assert cli.main(["--vers"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "usage"


COLLECT_CONFIG = {"region": REGION, "account": None, "volume_ids": [], "cost_basis": "public"}


def _response(service, operation, params, output):
    return {"service": service, "operation": operation, "input": params, "output": output}


def test_volume_reads_batch_by_200_and_follow_tokens():
    ids = [f"vol-{i:03}" for i in range(201)]
    first = {"Filters": [{"Name": "volume-id", "Values": ids[:200]}]}
    second = {"Filters": [{"Name": "volume-id", "Values": ids[200:]}]}
    aws = ReplayAws([
        _response("sts", "GetCallerIdentity", {}, {"Account": ACCOUNT}),
        _response("ec2", "DescribeVolumes", first, {"Volumes": [], "NextToken": "t1"}),
        _response("ec2", "DescribeVolumes", {**first, "NextToken": "t1"}, {"Volumes": []}),
        _response("ec2", "DescribeVolumes", second, {"Volumes": []}),
    ])
    collected, lookups = collect(aws, {**COLLECT_CONFIG, "volume_ids": ids}, None)
    assert [r["input"] for r in aws.reads[1:]] == [first, {**first, "NextToken": "t1"}, second]
    assert collected.volumes == dict.fromkeys(ids) and lookups is None and collected.prices is None
    looping = ReplayAws([
        _response("sts", "GetCallerIdentity", {}, {"Account": ACCOUNT}),
        _response("ec2", "DescribeVolumes", {"Filters": DISCOVERY}, {"Volumes": [], "NextToken": "t1"}),
        _response("ec2", "DescribeVolumes", {"Filters": DISCOVERY, "NextToken": "t1"},
                  {"Volumes": [], "NextToken": "t1"}),
    ])
    with pytest.raises(InputError, match="repeated pagination token"):
        collect(looping, COLLECT_CONFIG, None)


def test_instances_are_read_once_per_distinct_first_attachment_in_sorted_order():
    volumes = [volume("vol-a", attachments=[attachment("i-2")]),
               volume("vol-b", attachments=[attachment("i-1")]),
               volume("vol-c", attachments=[attachment("i-1"), attachment("i-3")])]
    aws = ReplayAws([
        _response("sts", "GetCallerIdentity", {}, {"Account": ACCOUNT}),
        _response("ec2", "DescribeVolumes", {"Filters": DISCOVERY}, to_jsonable({"Volumes": volumes})),
        _response("ec2", "DescribeInstances", {"Filters": [{"Name": "instance-id", "Values": ["i-1", "i-2"]}]},
                  to_jsonable({"Reservations": [{"Instances": [instance("i-1"), instance("i-2")]}]})),
        *[_response("pricing", "GetProducts", q, {"PriceList": [price_row(unit, usd)]}) for q, unit, usd in PRICE_ROWS],
    ])
    collected, lookups = collect(aws, COLLECT_CONFIG, None)
    assert sorted(collected.instances) == ["i-1", "i-2"]
    assert collected.selected_ids == ["vol-a", "vol-b", "vol-c"] and collected.discovery == "gp2_inventory"
    assert collected.instances["i-1"]["LaunchTime"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert collected.costs["vol-a"] == pytest.approx((120.0, 120.0)) and len(lookups.records) == 4

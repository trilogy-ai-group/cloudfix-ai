import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from cloudfix import cli
from cloudfix.common.aws import RecordingAws
from cloudfix.common.errors import InputError
from cloudfix.fixers.gp2_to_gp3 import plan as fixer
from cloudfix.fixers.gp2_to_gp3.plan import FIXER_OPS, load_assessment, make_change, make_plan, write_plan
from tests.factories import price_row, stubbed_client, volume
from tests.test_gp2_run import ACCOUNT, NOW, REGION, Stubs, _config

SNAPSHOT_QUERY = {"ServiceCode": "AmazonEC2", "FormatVersion": "aws_v1", "MaxResults": 100, "Filters": [
    {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage Snapshot"},
    {"Type": "TERM_MATCH", "Field": "regionCode", "Value": REGION}]}
FILTER = {"Filters": [{"Name": "volume-id", "Values": ["vol-1"]}]}
PLAN_NOW = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)


def _assess(tmp_path, tags=None):
    stubs = Stubs()
    stubs.identity()
    stubs.volumes([volume("vol-1", size=100, state="available", tags=tags)], FILTER["Filters"])
    stubs.prices()
    run_dir = tmp_path / "assessment"
    from cloudfix.finders.gp2_to_gp3.run import run_assess
    assert run_assess(_config(tmp_path, account=ACCOUNT, volume_ids=["vol-1"]), run_dir, aws=stubs.aws, now=NOW) == 0
    return run_dir


class Fixer:
    def __init__(self, live=None, mods=None, mods_error=None, snapshot="0.05", account=ACCOUNT):
        self.sts, self.sts_stub = stubbed_client("sts", REGION)
        self.sts_stub.add_response("get_caller_identity", {"Account": account, "Arn": "arn:aws:iam::1:user/t",
                                                           "UserId": "t"}, {})
        self.ec2, self.ec2_stub = stubbed_client("ec2", REGION)
        self.pricing, self.pricing_stub = stubbed_client("pricing", "us-east-1")
        self.aws = RecordingAws(REGION, FIXER_OPS, clients={"sts": self.sts, "ec2": self.ec2, "pricing": self.pricing})
        self.ec2_stub.add_response("describe_volumes", {"Volumes": [live or volume("vol-1", size=100, state="available")]},
                                   FILTER)
        if mods_error:
            self.ec2_stub.add_client_error("describe_volumes_modifications", service_error_code=mods_error,
                                           expected_params={"VolumeIds": ["vol-1"]})
        else:
            self.ec2_stub.add_response("describe_volumes_modifications", {"VolumesModifications": mods or []},
                                       {"VolumeIds": ["vol-1"]})
        self.pricing_stub.add_response("get_products", {"PriceList": [price_row("GB-Mo", snapshot)],
                                                        "FormatVersion": "aws_v1"}, SNAPSHOT_QUERY)


def _mod(state, start):
    return {"VolumeId": "vol-1", "ModificationState": state, "StartTime": start}


def _plan(assessment, fx, **kw):
    args = dict(volume_ids=None, management="not-managed", iops_overrides={}, throughput_overrides={}, now=PLAN_NOW)
    return make_plan(load_assessment(assessment), fx.aws, **{**args, **kw})


def test_plan_targets_match_assessment_and_hash_is_of_file_bytes(tmp_path):
    plan = _plan(_assess(tmp_path), Fixer(mods_error="InvalidVolumeModification.NotFound"))
    v = plan["volumes"][0]
    assert v["target"] == {"type": "gp3", "size_gib": 100, "iops": 3000, "throughput": 125}
    assert v["deviates_from_assessment"] is False and v["management_basis"] == "not_managed_by_code"
    assert v["estimate"]["annual_saving"] == pytest.approx(24.0) and v["steps"]
    path, digest = write_plan(tmp_path / "plan", plan)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    saved = json.loads(path.read_text())
    delta = datetime.fromisoformat(saved["expires_at"]) - datetime.fromisoformat(saved["created_at"])
    assert delta == timedelta(hours=24)
    assert saved["profile"] is None and saved["assessment"] == str((tmp_path / "assessment").resolve())


@pytest.mark.parametrize("tags,live,mods,cause", [
    ({"cloudfix:dontFixIt": "true"}, {}, [], "dontFixIt"),
    ({"ManagedBy": "terraform"}, {}, [], "PR worker"),
    (None, {"vtype": "gp3"}, [], "not gp2"),
    (None, {"size": 200}, [], "size"),
    (None, {}, [_mod("optimizing", PLAN_NOW - timedelta(days=3))], "in progress"),
    (None, {}, [_mod("completed", PLAN_NOW - timedelta(hours=5))], "6 hours"),
    (None, {"state": "creating"}, [], "creating"),
])
def test_plan_refusals(tmp_path, tags, live, mods, cause):
    fx = Fixer(live=volume("vol-1", **{"size": 100, "state": "available", "tags": tags, **live}), mods=mods)
    plan = _plan(_assess(tmp_path), fx)
    assert plan["volumes"] == [] and cause in plan["refused"][0]["reason"]


def test_latest_modification_decides(tmp_path):
    mods = [_mod("optimizing", PLAN_NOW - timedelta(days=9)), _mod("completed", PLAN_NOW - timedelta(days=2))]
    assert _plan(_assess(tmp_path), Fixer(mods=mods))["volumes"]


def test_all_refused_exits_1_and_writes_no_plan(tmp_path, monkeypatch, capsys):
    assessment = _assess(tmp_path)
    fx = Fixer(live=volume("vol-1", vtype="gp3", state="available"))
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: fx.aws)
    out = tmp_path / "plan"
    capsys.readouterr()
    code = cli.main(["gp2-to-gp3-fix", "plan", "--assessment", str(assessment), "--management", "not-managed",
                     "--volume-id", "vol-1", "--out", str(out)])
    assert code == 1 and not out.exists()
    assert json.loads(capsys.readouterr().out)["refused"][0]["volume_id"] == "vol-1"


def test_cli_plan_writes_plan(tmp_path, monkeypatch, capsys):
    assessment = _assess(tmp_path)
    fx = Fixer(mods_error="InvalidVolumeModification.NotFound")
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: fx.aws)
    out = tmp_path / "plan"
    capsys.readouterr()
    assert cli.main(["gp2-to-gp3-fix", "plan", "--assessment", str(assessment), "--management", "unknown",
                     "--volume-id", "vol-1", "--out", str(out), "--throughput", "vol-1=128"]) == 0
    line = json.loads(capsys.readouterr().out)
    assert line["status"] == "planned" and line["volumes"] == ["vol-1"]
    assert line["sha256"] == hashlib.sha256((out / "plan.json").read_bytes()).hexdigest()


def test_throughput_override_recomputes_estimate_and_marks_deviation(tmp_path):
    plan = _plan(_assess(tmp_path), Fixer(), throughput_overrides={"vol-1": 128})
    v = plan["volumes"][0]
    assert v["target"]["throughput"] == 128 and v["deviates_from_assessment"] is True
    assert v["assessed_target"] == {"iops": 3000, "throughput": 125}
    assert v["estimate"]["proposed_annual_cost"] == pytest.approx(96 + 12 * 3 * 0.04)
    assert v["estimate"]["display"]["proposed_annual_cost"]


@pytest.mark.parametrize("iops,throughput", [({"vol-1": 2000}, {}), ({}, {"vol-1": 2001}), ({}, {"vol-1": 1000})])
def test_override_outside_gp3_limits_is_refused(tmp_path, iops, throughput):
    with pytest.raises(InputError):
        _plan(_assess(tmp_path), Fixer(), iops_overrides=iops, throughput_overrides=throughput)


def test_override_for_unplanned_volume_is_an_error(tmp_path):
    with pytest.raises(InputError):
        _plan(_assess(tmp_path), Fixer(), iops_overrides={"vol-9": 3000})


@pytest.mark.parametrize("bad", ["vol-1", "vol-1=x", "=5", "vol-1=-3"])
def test_malformed_override_is_an_input_error(bad):
    with pytest.raises(InputError):
        cli._overrides([bad], "--iops")


def test_snapshot_cost_upper_bound(tmp_path):
    v = _plan(_assess(tmp_path), Fixer(snapshot="0.05"))["volumes"][0]
    assert v["snapshot_cost_upper_bound_monthly"] == pytest.approx(5.0) and v["snapshot_cost_display"] == "$5.00"


def test_incomplete_assessment_is_rejected(tmp_path):
    run_dir = _assess(tmp_path)
    ctx = json.loads((run_dir / "context.json").read_text())
    (run_dir / "context.json").write_text(json.dumps({**ctx, "status": "error"}))
    with pytest.raises(InputError):
        load_assessment(run_dir)


def test_change_json_has_change_note_and_iac_evidence(tmp_path, capsys):
    run_dir = _assess(tmp_path, tags={"ManagedBy": "terraform"})
    change = make_change(load_assessment(run_dir), volume_ids=None, iops_overrides={}, throughput_overrides={},
                         repo_hint="/repo", delivery="draft_pr")
    assert change["change_note"].endswith("skills/co-gp2-to-gp3-fix/iac.md")
    r = change["resources"][0]
    assert r["id"] == "vol-1" and r["iac_evidence"] and r["deviates_from_assessment"] is False
    assert r["target"]["iops"] == 3000 and change["delivery"] == "draft_pr" and change["repo_hint"] == "/repo"
    out = tmp_path / "change.json"
    assert cli.main(["gp2-to-gp3-fix", "change", "--assessment", str(run_dir), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["finder"] == "gp2-to-gp3"
    assert fixer.FIXER_OPS >= {"ec2.ModifyVolume", "ec2.DeleteSnapshot"}


def test_plan_records_identity_and_cost_basis(tmp_path):
    plan = _plan(_assess(tmp_path), Fixer())
    assert plan["caller_identity"]["Account"] == ACCOUNT
    assert plan["cost_basis"] == {"kind": "public_price_estimate", "source": None, "period": None}


def test_account_mismatch_is_an_input_error(tmp_path):
    with pytest.raises(InputError, match="account"):
        _plan(_assess(tmp_path), Fixer(account="999999999999"))


def test_profile_falls_back_to_context_config(tmp_path):
    run_dir = _assess(tmp_path)
    (run_dir / "config.json").unlink()
    ctx = json.loads((run_dir / "context.json").read_text())
    ctx["config"] = {**(ctx["config"] or {}), "profile": "audit"}
    (run_dir / "context.json").write_text(json.dumps(ctx))
    assert load_assessment(run_dir)["profile"] == "audit"
    assert _plan(run_dir, Fixer())["profile"] == "audit"


def test_cli_error_codes(tmp_path, monkeypatch, capsys):
    run_dir = _assess(tmp_path)
    capsys.readouterr()
    args = ["gp2-to-gp3-fix", "plan", "--assessment", str(run_dir), "--management", "unknown",
            "--volume-id", "vol-1", "--out", str(tmp_path / "p")]
    assert cli.main(args + ["--iops", "bad"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_input"
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: _Denied())
    assert cli.main(args) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert (err["code"], err["service"], err["operation"]) == ("UnauthorizedOperation", "sts", "GetCallerIdentity")


class _Denied:
    reads: list = []

    def call(self, service, operation, params):
        from cloudfix.common.errors import AwsCallError
        raise AwsCallError(service, operation, "UnauthorizedOperation", "denied")


def test_cli_plan_requires_volume_id(tmp_path, capsys):
    run_dir = _assess(tmp_path)
    capsys.readouterr()
    assert cli.main(["gp2-to-gp3-fix", "plan", "--assessment", str(run_dir), "--management", "not-managed",
                     "--out", str(tmp_path / "p")]) == 1
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["code"] == "usage" and "--volume-id" in err["message"]
    assert not (tmp_path / "p").exists()

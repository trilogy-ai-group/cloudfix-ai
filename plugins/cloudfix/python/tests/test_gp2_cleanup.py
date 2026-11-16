import json

import pytest

from cloudfix import cli
from cloudfix.common.errors import InputError
from cloudfix.fixers.gp2_to_gp3.apply import Approval
from cloudfix.fixers.gp2_to_gp3.cleanup import cleanup, status
from tests.test_gp2_apply import APPLY_NOW, OLD, Aws, Clock, _execution, _make_plan, _mod, _seed, _snap

APPROVAL = Approval("after_plan", "yes, delete the recovery snapshots")


def _tagged_snap(sid, vid, plan_id):
    return {**_snap(sid, vid, "completed"), "Tags": [{"Key": "cloudfix-ai:planId", "Value": plan_id}]}


def _read_snapshot(aws, sid, vid, plan_id):
    aws.stub.add_response("describe_snapshots", {"Snapshots": [_tagged_snap(sid, vid, plan_id)]},
                          {"SnapshotIds": [sid]})


def _delete(aws, sid):
    aws.stub.add_response("delete_snapshot", {}, {"SnapshotId": sid})


def test_status_makes_no_writes(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-2",))
    _seed(path, digest, {"vol-1": "verified", "vol-2": "optimizing"})
    aws = Aws(path, identity=False)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    aws.mods("vol-1", _mod("vol-1", "completed", APPLY_NOW))
    aws.volume("vol-2", vtype="gp3", iops=3000, throughput=125)
    aws.mods("vol-2", _mod("vol-2", "optimizing", APPLY_NOW, "Optimization in progress"))
    out = status(path, aws.aws)
    aws.assert_all_used()
    assert aws.operations() and all(op.startswith("Describe") for op in aws.operations())
    assert out["volumes"]["vol-1"] == {
        "state": "verified", "snapshot_id": "snap-1", "snapshot_deleted": False,
        "live": {"type": "gp3", "size_gib": 100, "iops": 3000, "throughput": 125},
        "modification": {"state": "completed", "message": None}}
    assert out["volumes"]["vol-2"]["state"] == "optimizing"
    assert out["volumes"]["vol-2"]["modification"]["message"] == "Optimization in progress"
    assert _execution(path)["volumes"]["vol-1"]["state"] == "verified"  # unchanged


def test_status_before_apply_reports_not_started(tmp_path):
    path, _ = _make_plan(tmp_path)
    aws = Aws(path, identity=False)
    aws.volume("vol-1")
    aws.mods("vol-1")
    out = status(path, aws.aws)
    assert out["volumes"]["vol-1"]["state"] == "not_started"
    assert out["volumes"]["vol-1"]["modification"] is None
    assert not (path.parent / "execution.json").exists()


def test_cleanup_deletes_only_verified_tagged_snapshots(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-2", "vol-3"))
    _seed(path, digest, {"vol-1": "verified", "vol-2": "optimizing", "vol-3": "verified"})
    aws = Aws(path)
    _read_snapshot(aws, "snap-1", "vol-1", aws.plan_id)
    _delete(aws, "snap-1")
    _read_snapshot(aws, "snap-3", "vol-3", "some-other-plan")
    out = cleanup(path, digest, APPROVAL, aws.aws, now=Clock().now)
    aws.assert_all_used()
    assert aws.operations().count("DeleteSnapshot") == 1
    assert out["deleted"] == ["vol-1"]
    assert out["skipped"] == {"vol-2": "not verified (state optimizing)",
                              "vol-3": "snapshot snap-3 carries a different cloudfix-ai:planId tag (tag mismatch)"}
    execution = _execution(path)
    assert execution["volumes"]["vol-1"]["snapshot_deleted"] is True
    assert "snapshot_deleted" not in execution["volumes"]["vol-3"]
    assert len(execution["cleanup"]) == 1
    assert execution["cleanup"][0]["approval"] == {"kind": "after_plan", "wording": APPROVAL.wording,
                                                   "recorded_at": APPLY_NOW.isoformat()}
    assert execution["cleanup"][0]["deleted"] == ["vol-1"]
    assert any(c["operation"] == "DeleteSnapshot" for c in execution["calls"])
    assert execution["volumes"]["vol-1"]["state"] == "verified"


def test_cleanup_skips_already_at_target_and_already_deleted(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-2",))
    _seed(path, digest, {"vol-1": "verified", "vol-2": "verified"})
    execution = _execution(path)
    execution["volumes"]["vol-1"].update(already_at_target=True, snapshot_id=None)
    execution["volumes"]["vol-2"]["snapshot_deleted"] = True
    (path.parent / "execution.json").write_text(json.dumps(execution))
    aws = Aws(path)
    out = cleanup(path, digest, APPROVAL, aws.aws, now=Clock().now)
    assert out["deleted"] == []
    assert "no recovery snapshot" in out["skipped"]["vol-1"]
    assert "already deleted" in out["skipped"]["vol-2"]
    assert "DeleteSnapshot" not in aws.operations()


def test_cleanup_hash_mismatch_refused(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "verified"})
    aws = Aws(path)
    with pytest.raises(InputError, match="SHA-256"):
        cleanup(path, "0" * 64, APPROVAL, aws.aws, now=Clock().now)
    assert aws.aws.reads == []


def test_cleanup_needs_an_execution_and_wording(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    with pytest.raises(InputError, match="execution"):
        cleanup(path, digest, APPROVAL, aws.aws, now=Clock().now)
    _seed(path, digest, {"vol-1": "verified"})
    with pytest.raises(InputError, match="wording"):
        cleanup(path, digest, Approval("after_plan", " "), aws.aws, now=Clock().now)
    assert aws.aws.reads == []


def test_cleanup_refuses_other_account(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "verified"})
    aws = Aws(path, account="999999999999")
    with pytest.raises(InputError, match="account"):
        cleanup(path, digest, APPROVAL, aws.aws, now=Clock().now)
    assert "DeleteSnapshot" not in aws.operations()


def test_cli_status_and_cleanup(tmp_path, monkeypatch, capsys):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "verified"})
    aws = Aws(path, identity=False)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    aws.mods("vol-1", _mod("vol-1", "completed", OLD))
    seen = []
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: (seen.append(k), aws.aws)[1])
    capsys.readouterr()
    assert cli.main(["gp2-to-gp3-fix", "status", "--plan", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["volumes"]["vol-1"]["state"] == "verified"
    assert seen[-1].get("retries", True) is True
    aws2 = Aws(path)
    _read_snapshot(aws2, "snap-1", "vol-1", aws2.plan_id)
    _delete(aws2, "snap-1")
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: (seen.append(k), aws2.aws)[1])
    monkeypatch.setattr(cli, "_utcnow", Clock().now)
    args = ["gp2-to-gp3-fix", "cleanup", "--plan", str(path), "--approval-kind", "after_plan",
            "--approval-text", "yes"]
    assert cli.main(args + ["--approve", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_input"
    assert cli.main(args + ["--approve", digest]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] == ["vol-1"]
    assert seen[-1]["retries"] is False


def test_cleanup_rerun_keeps_each_runs_record(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-2",))
    _seed(path, digest, {"vol-1": "verified", "vol-2": "optimizing"})
    first = Aws(path)
    _read_snapshot(first, "snap-1", "vol-1", first.plan_id)
    _delete(first, "snap-1")
    cleanup(path, digest, APPROVAL, first.aws, now=Clock().now)
    execution = _execution(path)
    execution["volumes"]["vol-2"]["state"] = "verified"
    (path.parent / "execution.json").write_text(json.dumps(execution))
    second = Aws(path)
    _read_snapshot(second, "snap-2", "vol-2", second.plan_id)
    _delete(second, "snap-2")
    later = Approval("prior", "delete the snapshots once verified")
    out = cleanup(path, digest, later, second.aws, now=Clock(APPLY_NOW.replace(hour=23)).now)
    assert out["deleted"] == ["vol-2"] and "already deleted" in out["skipped"]["vol-1"]
    runs = _execution(path)["cleanup"]
    assert [r["deleted"] for r in runs] == [["vol-1"], ["vol-2"]]
    assert [r["approval"]["kind"] for r in runs] == ["after_plan", "prior"]
    assert runs[0]["approval"]["wording"] == APPROVAL.wording
    assert runs[1]["approval"]["recorded_at"] != runs[0]["approval"]["recorded_at"]

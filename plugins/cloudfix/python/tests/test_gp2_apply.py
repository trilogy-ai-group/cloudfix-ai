import copy
import json
from datetime import timedelta

import pytest

from cloudfix import cli
from cloudfix.common.aws import RecordingAws
from cloudfix.common.errors import InputError
from cloudfix.fixers.gp2_to_gp3.apply import DEFAULT_WAIT_MINUTES, POLL_SECONDS, STATES, Approval, apply_plan
from cloudfix.fixers.gp2_to_gp3.plan import FIXER_OPS, FIXER_READS, FIXER_WRITES, load_assessment, make_plan, write_plan
from tests.factories import offline_aws_env, stubbed_client, volume
from tests.test_gp2_plan import PLAN_NOW, Fixer, _assess
from tests.test_gp2_run import ACCOUNT, REGION

APPLY_NOW = PLAN_NOW + timedelta(hours=1)
OLD = PLAN_NOW - timedelta(days=30)
DATE = "2026-11-16"
WRITES = {"CreateSnapshot", "ModifyVolume", "CreateTags", "DeleteSnapshot"}
AFTER_PLAN = Approval("after_plan", "yes, apply this plan")


class Clock:
    """`now` starts at a fixed time; `sleep` records each call and moves the clock forward."""

    def __init__(self, start=APPLY_NOW):
        self.t, self.sleeps = start, []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)


def _tags(plan_id, vid):
    return [{"Key": "cloudfix-ai:fixer", "Value": "gp2-to-gp3"},
            {"Key": "cloudfix-ai:originalResourceId", "Value": vid},
            {"Key": "cloudfix-ai:executionDate", "Value": DATE},
            {"Key": "cloudfix-ai:planId", "Value": plan_id}]


def _snap(sid, vid, state, message=None):
    out = {"SnapshotId": sid, "VolumeId": vid, "State": state, "StartTime": APPLY_NOW, "VolumeSize": 100}
    if message:
        out["StateMessage"] = message
    return out


def _mod(vid, state, start, message=None):
    out = {"VolumeId": vid, "ModificationState": state, "StartTime": start, "OriginalVolumeType": "gp2",
           "TargetVolumeType": "gp3", "TargetIops": 3000, "TargetThroughput": 125, "TargetSize": 100}
    if message:
        out["StatusMessage"] = message
    return out


class Aws:
    """Stubbed sts and ec2 behind a RecordingAws; calls are queued in the order apply makes them."""

    def __init__(self, plan_path, *, account=ACCOUNT, identity=True):
        self.plan_id = json.loads(plan_path.read_text())["plan_id"]
        self.sts, self.sts_stub = stubbed_client("sts", REGION)
        self.ec2, self.stub = stubbed_client("ec2", REGION)
        self.aws = RecordingAws(REGION, FIXER_OPS, clients={"sts": self.sts, "ec2": self.ec2})
        if identity:
            self.sts_stub.add_response("get_caller_identity", {
                "Account": account, "Arn": f"arn:aws:iam::{account}:user/tester", "UserId": "tester"}, {})

    def volume(self, vid, **kw):
        live = volume(vid, **{"size": 100, "state": "available", **kw})
        self.stub.add_response("describe_volumes", {"Volumes": [live]},
                               {"Filters": [{"Name": "volume-id", "Values": [vid]}]})

    def mods(self, vid, *mods):
        if mods:
            self.stub.add_response("describe_volumes_modifications", {"VolumesModifications": list(mods)},
                                   {"VolumeIds": [vid]})
        else:
            self.stub.add_client_error("describe_volumes_modifications",
                                       service_error_code="InvalidVolumeModification.NotFound",
                                       expected_params={"VolumeIds": [vid]})

    def tagged(self, vid, *snaps):
        self.stub.add_response("describe_snapshots", {"Snapshots": list(snaps)}, {"OwnerIds": ["self"], "Filters": [
            {"Name": "tag:cloudfix-ai:planId", "Values": [self.plan_id]},
            {"Name": "tag:cloudfix-ai:originalResourceId", "Values": [vid]}]})

    def create_snapshot(self, vid, sid, error=None):
        params = {"VolumeId": vid, "Description": f"cloudfix-ai gp2-to-gp3 recovery point {self.plan_id}",
                  "TagSpecifications": [{"ResourceType": "snapshot", "Tags": _tags(self.plan_id, vid)}]}
        if error:
            self.stub.add_client_error("create_snapshot", service_error_code=error, service_message="boom",
                                       expected_params=params)
        else:
            self.stub.add_response("create_snapshot", _snap(sid, vid, "pending"), params)

    def snapshot(self, sid, vid, state, message=None):
        self.stub.add_response("describe_snapshots", {"Snapshots": [_snap(sid, vid, state, message)]},
                               {"SnapshotIds": [sid]})

    def modify(self, vid, error=None):
        params = {"VolumeId": vid, "VolumeType": "gp3", "Iops": 3000, "Throughput": 125}
        if error:
            self.stub.add_client_error("modify_volume", service_error_code=error, service_message="boom",
                                       expected_params=params)
        else:
            self.stub.add_response("modify_volume", {"VolumeModification": _mod(vid, "modifying", APPLY_NOW)},
                                   params)

    def create_tags(self, vid):
        self.stub.add_response("create_tags", {}, {"Resources": [vid], "Tags": _tags(self.plan_id, vid)})

    # The usual sequence, in chunks.
    def start(self, vid, sid):  # planned -> snapshot_requested
        self.volume(vid)
        self.mods(vid)
        self.tagged(vid)
        self.create_snapshot(vid, sid)

    def submit(self, vid):  # snapshot_completed -> modification_submitted
        self.mods(vid, _mod(vid, "completed", OLD))
        self.volume(vid)
        self.modify(vid)

    def modification(self, vid, state, message=None):
        self.mods(vid, _mod(vid, "completed", OLD), _mod(vid, state, APPLY_NOW, message))

    def verify(self, vid):  # completed -> verified
        self.volume(vid, vtype="gp3", iops=3000, throughput=125)
        self.create_tags(vid)

    def operations(self):
        return [r["operation"] for r in self.aws.reads]

    def assert_all_used(self):
        self.sts_stub.assert_no_pending_responses()
        self.stub.assert_no_pending_responses()


def _make_plan(tmp_path, *, management="not-managed", throughput=None, extra=()):
    fx = Fixer(mods_error="InvalidVolumeModification.NotFound")
    plan = make_plan(load_assessment(_assess(tmp_path)), fx.aws, volume_ids=None, management=management,
                     iops_overrides={}, throughput_overrides=throughput or {}, now=PLAN_NOW)
    for vid in extra:
        plan["volumes"].append({**copy.deepcopy(plan["volumes"][0]), "volume_id": vid})
    return write_plan(tmp_path / "fix", plan)


def _apply(path, digest, aws, clock=None, *, approval=AFTER_PLAN, confirm=False, wait=15):
    clock = clock or Clock()
    return apply_plan(path, digest, approval, aws.aws, confirm_direct=confirm, wait_minutes=wait,
                      now=clock.now, sleep=clock.sleep)


def _execution(path):
    return json.loads((path.parent / "execution.json").read_text())


def _seed(path, digest, states):
    """Write execution.json as an earlier run would have left it, each volume at the given state."""
    plan_id = json.loads(path.read_text())["plan_id"]
    volumes = {vid: {"state": state, "snapshot_id": None if state == "planned" else f"snap-{vid[-1]}",
                     "already_at_target": False,
                     "history": [{"state": s, "at": APPLY_NOW.isoformat()} for s in STATES[:STATES.index(state) + 1]]}
               for vid, state in states.items()}
    (path.parent / "execution.json").write_text(json.dumps({
        "plan_id": plan_id, "plan_sha256": digest,
        "approval": {"kind": "after_plan", "wording": "yes", "recorded_at": APPLY_NOW.isoformat()},
        "confirmed_direct_management": False, "volumes": volumes, "calls": []}))


def test_hash_mismatch_refused_before_any_call(tmp_path):
    path, _ = _make_plan(tmp_path)
    aws = Aws(path)
    with pytest.raises(InputError, match="SHA-256"):
        _apply(path, "0" * 64, aws)
    assert aws.aws.reads == []
    assert not (path.parent / "execution.json").exists()


@pytest.mark.parametrize("plan_kw,confirm", [
    ({"throughput": {"vol-1": 128}}, False),
    ({"management": "unknown"}, True),
])
def test_prior_approval_refused_for_override_or_unknown_management(tmp_path, plan_kw, confirm):
    path, digest = _make_plan(tmp_path, **plan_kw)
    aws = Aws(path)
    with pytest.raises(InputError, match="earlier approval"):
        _apply(path, digest, aws, approval=Approval("prior", "convert all approved volumes"), confirm=confirm)
    assert aws.aws.reads == []


def test_prior_approval_accepted_for_assessed_unmanaged_plan(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    result = _apply(path, digest, aws, approval=Approval("prior", "convert all approved volumes"))
    assert result["status"] == "completed"
    approval = _execution(path)["approval"]
    assert (approval["kind"], approval["wording"]) == ("prior", "convert all approved volumes")
    assert approval["recorded_at"] == APPLY_NOW.isoformat()


def test_empty_approval_wording_refused(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    with pytest.raises(InputError, match="wording"):
        _apply(path, digest, aws, approval=Approval("after_plan", "  "))
    assert aws.aws.reads == []


def test_unknown_management_requires_confirm_direct(tmp_path):
    path, digest = _make_plan(tmp_path, management="unknown")
    refused = Aws(path)
    with pytest.raises(InputError, match="confirm"):
        _apply(path, digest, refused)
    assert refused.aws.reads == []
    confirmed = Aws(path)
    confirmed.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    assert _apply(path, digest, confirmed, confirm=True)["status"] == "completed"
    assert _execution(path)["confirmed_direct_management"] is True


def test_happy_path_single_run(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.start("vol-1", "snap-1")
    aws.snapshot("snap-1", "vol-1", "pending")
    aws.snapshot("snap-1", "vol-1", "completed")
    aws.submit("vol-1")
    aws.modification("vol-1", "modifying")
    aws.modification("vol-1", "optimizing")
    aws.modification("vol-1", "completed")
    aws.verify("vol-1")
    clock = Clock()
    result = _apply(path, digest, aws, clock)
    aws.assert_all_used()
    assert result["status"] == "completed"
    assert result["volumes"] == {"vol-1": {"state": "verified", "snapshot_id": "snap-1"}}
    assert result["execution_file"] == str(path.parent / "execution.json")
    assert clock.sleeps and set(clock.sleeps) == {POLL_SECONDS}
    reads = aws.aws.reads
    tags = _tags(aws.plan_id, "vol-1")
    create = next(r for r in reads if r["operation"] == "CreateSnapshot")["input"]
    assert create["TagSpecifications"] == [{"ResourceType": "snapshot", "Tags": tags}]
    assert not any(t["Key"].startswith("cloudfix:") for t in create["TagSpecifications"][0]["Tags"])
    assert next(r for r in reads if r["operation"] == "CreateTags")["input"] == {"Resources": ["vol-1"], "Tags": tags}
    execution = _execution(path)
    record = execution["volumes"]["vol-1"]
    assert [h["state"] for h in record["history"]] == list(STATES)
    assert record["already_at_target"] is False
    assert record["final"] == {"type": "gp3", "size_gib": 100, "iops": 3000, "throughput": 125}
    assert execution["plan_sha256"] == digest and execution["plan_id"] == aws.plan_id
    assert [c["operation"] for c in execution["calls"]] == aws.operations()


def test_in_progress_then_resume(tmp_path, monkeypatch, capsys):
    path, digest = _make_plan(tmp_path)
    first = Aws(path)
    first.start("vol-1", "snap-1")
    made = []
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: made.append((a, k)) or first.aws)
    monkeypatch.setattr(cli, "_utcnow", Clock().now)
    capsys.readouterr()
    code = cli.main(["gp2-to-gp3-fix", "apply", "--plan", str(path), "--approve", digest,
                     "--approval-kind", "after_plan", "--approval-text", "yes, go ahead", "--wait", "0"])
    line = json.loads(capsys.readouterr().out)
    assert code == 3
    assert line["status"] == "in_progress"
    assert line["volumes"]["vol-1"] == {"state": "snapshot_requested", "snapshot_id": "snap-1"}
    assert line["execution_file"] == str(path.parent / "execution.json")
    assert made == [((REGION, FIXER_OPS), {"profile": None, "no_retry": FIXER_WRITES})]
    second = Aws(path)
    second.snapshot("snap-1", "vol-1", "completed")
    second.submit("vol-1")
    second.modification("vol-1", "completed")
    second.verify("vol-1")
    assert _apply(path, digest, second)["status"] == "completed"
    second.assert_all_used()
    assert "CreateSnapshot" not in second.operations()
    calls = [c["operation"] for c in _execution(path)["calls"]]
    assert calls.count("CreateSnapshot") == 1 and calls.count("GetCallerIdentity") == 2


def test_lost_create_snapshot_response_found_by_tag(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1")
    aws.mods("vol-1")
    aws.tagged("vol-1")
    aws.create_snapshot("vol-1", "snap-1", error="RequestTimeout")
    aws.tagged("vol-1", _snap("snap-1", "vol-1", "pending"))
    aws.snapshot("snap-1", "vol-1", "pending")  # one poll (wait = one poll interval) uses snap-1
    result = _apply(path, digest, aws, wait=POLL_SECONDS / 60)
    aws.assert_all_used()
    assert result["status"] == "in_progress"
    assert result["volumes"]["vol-1"] == {"state": "snapshot_requested", "snapshot_id": "snap-1"}
    assert aws.operations().count("CreateSnapshot") == 1
    assert [c["operation"] for c in _execution(path)["calls"]].count("CreateSnapshot") == 1


@pytest.mark.parametrize("seeded", [True, False])
def test_snapshot_created_before_a_crash_is_found_by_tag(tmp_path, seeded):
    """The process died after CreateSnapshot succeeded, before execution.json recorded it."""
    path, digest = _make_plan(tmp_path)
    if seeded:
        _seed(path, digest, {"vol-1": "planned"})
    aws = Aws(path)
    aws.volume("vol-1")
    aws.mods("vol-1")
    aws.tagged("vol-1", _snap("snap-1", "vol-1", "pending"))
    result = _apply(path, digest, aws, wait=0)
    aws.assert_all_used()
    assert "CreateSnapshot" not in aws.operations()
    assert result["volumes"]["vol-1"] == {"state": "snapshot_requested", "snapshot_id": "snap-1"}


def test_apply_caller_sends_writes_once_and_keeps_retries_for_reads(tmp_path, monkeypatch):
    offline_aws_env(monkeypatch, tmp_path)
    path, _ = _make_plan(tmp_path)
    built = []

    def build(*args, **kwargs):
        built.append(RecordingAws(*args, **kwargs))
        return built[-1]

    monkeypatch.setattr(cli, "RecordingAws", build)
    code = cli.main(["gp2-to-gp3-fix", "apply", "--plan", str(path), "--approve", "0" * 64,
                     "--approval-kind", "after_plan", "--approval-text", "yes"])
    assert code == 1 and built[0].reads == []  # refused on the hash, before any request
    assert FIXER_WRITES == {"ec2.CreateSnapshot", "ec2.ModifyVolume", "ec2.CreateTags", "ec2.DeleteSnapshot"}
    for op in FIXER_WRITES:
        assert built[0]._client(*op.split(".")).meta.config.retries["total_max_attempts"] == 1
    for op in FIXER_READS:
        assert built[0]._client(*op.split(".")).meta.config.retries.get("total_max_attempts") is None


def test_default_wait_returns_in_progress_within_about_a_minute(tmp_path, monkeypatch, capsys):
    """The default --wait must end well inside a 2-minute harness command timeout."""
    assert DEFAULT_WAIT_MINUTES == 1
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.start("vol-1", "snap-1")
    for _ in range(4):  # one poll at the start of each 15-second interval of the minute
        aws.snapshot("snap-1", "vol-1", "pending")
    clock = Clock()
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: aws.aws)
    monkeypatch.setattr(cli, "_utcnow", clock.now)
    monkeypatch.setattr(cli.time, "sleep", clock.sleep)
    capsys.readouterr()
    code = cli.main(["gp2-to-gp3-fix", "apply", "--plan", str(path), "--approve", digest,
                     "--approval-kind", "after_plan", "--approval-text", "yes"])
    aws.assert_all_used()
    assert code == 3 and json.loads(capsys.readouterr().out)["status"] == "in_progress"
    assert sum(clock.sleeps) == 60


def test_target_reached_after_the_snapshot_is_not_already_at_target(tmp_path):
    """The volume reached the target without a modification of ours after the snapshot was taken: the
    snapshot exists, so the record must not claim that none was taken."""
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "snapshot_completed"})
    aws = Aws(path)
    aws.mods("vol-1", _mod("vol-1", "completed", OLD))  # older than the plan: not ours
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["volumes"]["vol-1"] == {"state": "verified", "snapshot_id": "snap-1"}
    assert not set(aws.operations()) & WRITES
    record = _execution(path)["volumes"]["vol-1"]
    assert record["already_at_target"] is False and record["snapshot_id"] == "snap-1"


def test_create_snapshot_error_without_tagged_snapshot_fails_volume(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1")
    aws.mods("vol-1")
    aws.tagged("vol-1")
    aws.create_snapshot("vol-1", "snap-1", error="SnapshotLimitExceeded")
    aws.tagged("vol-1")
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["status"] == "failed"
    assert "SnapshotLimitExceeded" in result["volumes"]["vol-1"]["reason"]
    assert aws.operations().count("CreateSnapshot") == 1


def test_lost_modify_response_found_in_history(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "snapshot_completed"})
    aws = Aws(path)
    aws.modification("vol-1", "modifying")
    result = _apply(path, digest, aws, wait=0)
    aws.assert_all_used()
    assert "ModifyVolume" not in aws.operations()
    assert result["status"] == "in_progress"
    assert result["volumes"]["vol-1"]["state"] == "modification_submitted"


@pytest.mark.parametrize("found,state", [(True, "modification_submitted"), (False, "failed")])
def test_modify_error_resolved_through_history(tmp_path, found, state):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "snapshot_completed"})
    aws = Aws(path)
    aws.mods("vol-1", _mod("vol-1", "completed", OLD))
    aws.volume("vol-1")
    aws.modify("vol-1", error="RequestTimeout")
    if found:
        aws.modification("vol-1", "modifying")
    else:
        aws.mods("vol-1", _mod("vol-1", "completed", OLD))
    result = _apply(path, digest, aws, wait=0)
    aws.assert_all_used()
    assert aws.operations().count("ModifyVolume") == 1
    assert result["volumes"]["vol-1"]["state"] == state
    if not found:
        assert "RequestTimeout" in result["volumes"]["vol-1"]["reason"]


def test_already_at_target_verified_without_writes(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["status"] == "completed"
    assert not set(aws.operations()) & WRITES
    record = _execution(path)["volumes"]["vol-1"]
    assert record["already_at_target"] is True and record["state"] == "verified"
    assert [h["state"] for h in record["history"]] == ["planned", "verified"]


def test_optimizing_is_in_progress_not_done(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "modification_submitted"})
    aws = Aws(path)
    aws.modification("vol-1", "optimizing")
    result = _apply(path, digest, aws, wait=0)
    aws.assert_all_used()
    assert result["status"] == "in_progress"
    assert result["volumes"]["vol-1"]["state"] == "optimizing"


def test_one_volume_fails_others_complete(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-2",))
    aws = Aws(path)
    aws.start("vol-1", "snap-1")
    aws.start("vol-2", "snap-2")
    aws.snapshot("snap-1", "vol-1", "completed")
    aws.submit("vol-1")
    aws.snapshot("snap-2", "vol-2", "completed")
    aws.submit("vol-2")
    aws.modification("vol-1", "failed", "Insufficient capacity for the requested change")
    aws.modification("vol-2", "completed")
    aws.verify("vol-2")
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["status"] == "failed"
    assert result["volumes"]["vol-2"] == {"state": "verified", "snapshot_id": "snap-2"}
    assert result["volumes"]["vol-1"]["state"] == "failed"
    assert "Insufficient capacity for the requested change" in result["volumes"]["vol-1"]["reason"]
    rerun = Aws(path, identity=False)
    again = _apply(path, digest, rerun)
    assert rerun.aws.reads == []
    assert again["volumes"] == result["volumes"] and again["status"] == "failed"


def test_account_mismatch_refused_before_writes(tmp_path):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path, account="999999999999")
    with pytest.raises(InputError, match="account"):
        _apply(path, digest, aws)
    assert aws.operations() == ["GetCallerIdentity"]
    assert not any(r["service"] == "ec2" for r in aws.aws.reads)
    assert not (path.parent / "execution.json").exists()


def test_expired_plan_refused_to_start_but_resumes_when_started(tmp_path):
    path, digest = _make_plan(tmp_path)
    late = PLAN_NOW + timedelta(hours=25)
    fresh = Aws(path)
    with pytest.raises(InputError, match="expired"):
        _apply(path, digest, fresh, Clock(late))
    assert fresh.aws.reads == []
    _seed(path, digest, {"vol-1": "snapshot_requested"})
    resumed = Aws(path)
    resumed.snapshot("snap-1", "vol-1", "completed")
    resumed.submit("vol-1")
    result = _apply(path, digest, resumed, Clock(late), wait=0)
    resumed.assert_all_used()
    assert result["status"] == "in_progress"
    assert result["volumes"]["vol-1"]["state"] == "modification_submitted"


RESUME = {  # state -> (chunks to queue, writes expected)
    "planned": (lambda a: (a.start("vol-1", "snap-1"), a.snapshot("snap-1", "vol-1", "completed"), a.submit("vol-1")),
                ["CreateSnapshot", "ModifyVolume", "CreateTags"]),
    "snapshot_requested": (lambda a: (a.snapshot("snap-1", "vol-1", "completed"), a.submit("vol-1")),
                           ["ModifyVolume", "CreateTags"]),
    "snapshot_completed": (lambda a: a.submit("vol-1"), ["ModifyVolume", "CreateTags"]),
    "modification_submitted": (lambda a: None, ["CreateTags"]),
    "optimizing": (lambda a: None, ["CreateTags"]),
    "completed": (None, ["CreateTags"]),
}


@pytest.mark.parametrize("state", STATES[:-1])
def test_resume_from_every_state(tmp_path, state):
    path, digest = _make_plan(tmp_path)
    if state != "planned":
        _seed(path, digest, {"vol-1": state})
    aws = Aws(path)
    queue, writes = RESUME[state]
    if queue is not None:
        queue(aws)
        aws.modification("vol-1", "completed")
    aws.verify("vol-1")
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["volumes"]["vol-1"]["state"] == "verified"
    assert [op for op in aws.operations() if op in WRITES] == writes
    history = [h["state"] for h in _execution(path)["volumes"]["vol-1"]["history"]]
    seen_optimizing = STATES.index(state) >= STATES.index("optimizing")  # only the seeded history holds it
    assert history == [s for s in STATES if s != "optimizing" or seen_optimizing]


@pytest.mark.parametrize("live,cause", [
    ({"vtype": "io1"}, "not gp2"),
    ({"size": 200}, "size"),
    ({"tags": {"cloudfix:dontFixIt": "true"}}, "dontFixIt"),
])
def test_live_state_changed_fails_without_writes(tmp_path, live, cause):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1", **live)
    aws.mods("vol-1")
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["status"] == "failed" and cause in result["volumes"]["vol-1"]["reason"]
    assert not set(aws.operations()) & WRITES


def test_snapshot_error_fails_volume(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "snapshot_requested"})
    aws = Aws(path)
    aws.snapshot("snap-1", "vol-1", "error", "internal snapshot failure")
    result = _apply(path, digest, aws)
    assert result["status"] == "failed"
    assert "internal snapshot failure" in result["volumes"]["vol-1"]["reason"]


def test_verification_mismatch_fails_without_tags(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "completed"})
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=4000, throughput=125)
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert result["status"] == "failed" and "4000" in result["volumes"]["vol-1"]["reason"]
    assert "CreateTags" not in aws.operations()


def test_tags_already_on_volume_are_not_written_again(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "completed"})
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125, tags={"cloudfix-ai:planId": aws.plan_id})
    assert _apply(path, digest, aws)["status"] == "completed"
    assert "CreateTags" not in aws.operations()


def test_execution_for_another_plan_is_refused(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, "f" * 64, {"vol-1": "snapshot_requested"})
    aws = Aws(path)
    with pytest.raises(InputError, match="different plan"):
        _apply(path, digest, aws)
    assert aws.aws.reads == []


def test_unknown_state_in_execution_is_refused(tmp_path):
    path, digest = _make_plan(tmp_path)
    _seed(path, digest, {"vol-1": "snapshot_requested"})
    execution = _execution(path)
    execution["volumes"]["vol-1"]["state"] = "__init__"
    (path.parent / "execution.json").write_text(json.dumps(execution))
    aws = Aws(path)
    with pytest.raises(InputError, match="unknown state"):
        _apply(path, digest, aws)
    assert aws.aws.reads == []


def test_duplicate_volume_entry_is_processed_once(tmp_path):
    path, digest = _make_plan(tmp_path, extra=("vol-1",))
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    result = _apply(path, digest, aws)
    aws.assert_all_used()
    assert list(result["volumes"]) == ["vol-1"] and result["status"] == "completed"


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    path, digest = _make_plan(tmp_path)
    aws = Aws(path)
    aws.volume("vol-1", vtype="gp3", iops=3000, throughput=125)
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: aws.aws)
    monkeypatch.setattr(cli, "_utcnow", Clock().now)
    args = ["gp2-to-gp3-fix", "apply", "--plan", str(path), "--approval-kind", "after_plan",
            "--approval-text", "yes"]
    capsys.readouterr()
    assert cli.main(args + ["--approve", "0" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_input"
    assert cli.main(args + ["--approve", digest, "--wait", "-1"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_input"
    assert cli.main(args + ["--approve", digest]) == 0
    assert json.loads(capsys.readouterr().out)["volumes"]["vol-1"]["state"] == "verified"
    _seed(path, digest, {"vol-1": "completed"})
    failing = Aws(path)
    failing.volume("vol-1", vtype="gp3", iops=4000, throughput=125)
    monkeypatch.setattr(cli, "RecordingAws", lambda *a, **k: failing.aws)
    assert cli.main(args + ["--approve", digest]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"

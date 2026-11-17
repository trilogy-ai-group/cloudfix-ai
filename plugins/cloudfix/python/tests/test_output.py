import json
import os
import stat
from datetime import datetime, timezone

import pytest

from cloudfix.common.errors import InputError
from cloudfix.common.output import emit, new_run_dir, to_jsonable, write_json

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")


def test_new_run_dir_creates(tmp_path):
    d = new_run_dir(tmp_path / "run")
    assert d.is_dir() and d == (tmp_path / "run").resolve()


def test_new_run_dir_rejects_existing(tmp_path):
    (tmp_path / "run").mkdir()
    with pytest.raises(InputError):
        new_run_dir(tmp_path / "run")


def test_new_run_dir_rejects_missing_parent(tmp_path):
    with pytest.raises(InputError):
        new_run_dir(tmp_path / "nope" / "run")


def test_write_json_format_and_datetimes(tmp_path):
    p = tmp_path / "a.json"
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    write_json(p, {"t": when, "l": (when,)})
    text = p.read_text()
    assert text.endswith("\n")
    assert text == json.dumps({"t": when.isoformat(), "l": [when.isoformat()]}, indent=2) + "\n"


@posix_only
def test_modes(tmp_path):
    d = new_run_dir(tmp_path / "run")
    write_json(d / "a.json", {})
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    assert stat.S_IMODE((d / "a.json").stat().st_mode) == 0o600


def test_to_jsonable_dataclass():
    from dataclasses import dataclass

    @dataclass
    class X:
        when: datetime

    assert to_jsonable(X(datetime(2026, 1, 1))) == {"when": "2026-01-01T00:00:00"}


def test_emit_one_line(capsys):
    emit({"a": 1, "b": [1, 2]})
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and json.loads(out) == {"a": 1, "b": [1, 2]}


def test_write_json_failure_mid_write_keeps_the_previous_file(tmp_path, monkeypatch):
    p = tmp_path / "execution.json"
    write_json(p, {"state": "snapshot_requested"})
    before = p.read_bytes()

    def broken_dump(obj, fp, **kwargs):
        fp.write('{"state": "trunc')  # a partial write into the open temporary file, then a failure
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", broken_dump)
    with pytest.raises(OSError, match="disk full"):
        write_json(p, {"state": "verified"})
    assert p.read_bytes() == before
    assert [f.name for f in tmp_path.iterdir()] == ["execution.json"]  # no temporary file left behind


@posix_only
def test_write_json_replacing_a_file_keeps_mode_0600(tmp_path):
    p = tmp_path / "a.json"
    p.write_text("{}")
    p.chmod(0o644)
    write_json(p, {"b": 1})
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and json.loads(p.read_text()) == {"b": 1}

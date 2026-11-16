import json

import pytest

from cloudfix.common.errors import InputError
from cloudfix.common.settings import Setting, describe, load_config

SETTINGS = [
    Setting("account", "Which account?", "account_id", required=True),
    Setting("region", "Which region?", "region", default="us-east-1"),
    Setting("count", "How many?", "integer", default=5, minimum=1),
    Setting("mode", "Which mode?", "choice", default="a", choices=("a", "b")),
]


def _cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data))
    return p


def test_describe_keys():
    d = describe(SETTINGS)
    assert [x["name"] for x in d] == ["account", "region", "count", "mode"]
    assert set(d[0]) == {"name", "question", "type", "default", "required", "choices", "minimum"}
    assert d[0]["required"] is True and d[3]["choices"] == ["a", "b"]
    assert d[2]["minimum"] == 1


def test_load_config_fills_defaults(tmp_path):
    cfg = load_config(_cfg(tmp_path, {"account": "123456789012"}), SETTINGS)
    assert cfg == {"account": "123456789012", "region": "us-east-1", "count": 5, "mode": "a"}


def test_rejects_unknown_key(tmp_path):
    with pytest.raises(InputError, match="unknown setting: x"):
        load_config(_cfg(tmp_path, {"account": "123456789012", "x": 1}), SETTINGS)


def test_rejects_missing_required(tmp_path):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {}), SETTINGS)


@pytest.mark.parametrize("bad", ["12345", "12345678901a", "1234567890123", 123456789012])
def test_rejects_bad_account(tmp_path, bad):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {"account": bad}), SETTINGS)


@pytest.mark.parametrize("bad", ["us-east", "US-EAST-1", "useast1", "us-east-12"])
def test_rejects_bad_region(tmp_path, bad):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {"account": "123456789012", "region": bad}), SETTINGS)


def test_accepts_multi_part_region(tmp_path):
    cfg = load_config(_cfg(tmp_path, {"account": "123456789012", "region": "ap-southeast-2"}), SETTINGS)
    assert cfg["region"] == "ap-southeast-2"


def test_rejects_integer_below_minimum(tmp_path):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {"account": "123456789012", "count": 0}), SETTINGS)


def test_rejects_non_integer(tmp_path):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {"account": "123456789012", "count": True}), SETTINGS)


def test_rejects_bad_choice(tmp_path):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, {"account": "123456789012", "mode": "z"}), SETTINGS)


def test_rejects_non_object_and_bad_json(tmp_path):
    with pytest.raises(InputError):
        load_config(_cfg(tmp_path, [1]), SETTINGS)
    p = tmp_path / "bad.json"
    p.write_text("{")
    with pytest.raises(InputError):
        load_config(p, SETTINGS)

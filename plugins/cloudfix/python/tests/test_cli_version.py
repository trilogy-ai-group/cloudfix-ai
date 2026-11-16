import json
from cloudfix import cli, __version__

def test_version_prints_one_json_line(capsys):
    assert cli.main(["--version"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out) == {"package": "cloudfix", "version": __version__}

def test_no_arguments_prints_usage_and_returns_zero(capsys):
    assert cli.main([]) == 0
    assert "usage: cloudfix" in capsys.readouterr().out

def test_unknown_command_is_a_json_error(capsys):
    assert cli.main(["nope"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"

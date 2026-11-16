import json
from pathlib import Path

from cloudfix import __version__

REPO = Path(__file__).resolve().parents[4]


def test_versions_agree():
    for p in ("plugins/cloudfix/.claude-plugin/plugin.json", "plugins/cloudfix/.codex-plugin/plugin.json"):
        assert json.loads((REPO / p).read_text())["version"] == __version__


def test_both_marketplaces_point_at_the_plugin():
    claude = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    codex = json.loads((REPO / ".agents/plugins/marketplace.json").read_text())
    assert claude["plugins"][0]["source"] == "./plugins/cloudfix"
    assert codex["plugins"][0]["source"] == {"source": "local", "path": "./plugins/cloudfix"}
    assert claude["name"] == codex["name"] == "cloudfix-ai"


def test_old_bundle_is_gone():
    assert not (REPO / "skills/co-gp2-to-gp3").exists()

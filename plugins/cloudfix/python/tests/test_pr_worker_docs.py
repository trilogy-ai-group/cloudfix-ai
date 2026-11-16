# tests/test_pr_worker_docs.py
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]


def test_worker_skill_is_finder_agnostic():
    text = (PLUGIN / "skills/co-iac-pr/SKILL.md").read_text().lower()
    for word in ("gp2", "gp3", "ebs", "volume"):
        assert word not in text


def test_agents_point_at_skill_sections():
    worker = (PLUGIN / "agents/iac-pr-worker.md").read_text()
    reviewer = (PLUGIN / "agents/iac-pr-reviewer.md").read_text()
    assert "co-iac-pr" in worker and "## Worker" in worker
    assert "co-iac-pr" in reviewer and "## Reviewer" in reviewer
    assert "Edit" not in reviewer.split("---")[1] and "Write" not in reviewer.split("---")[1]  # read-only tools in frontmatter

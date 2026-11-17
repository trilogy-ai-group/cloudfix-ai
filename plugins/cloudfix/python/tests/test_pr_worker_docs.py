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


def _tools(agent: str) -> list[str]:
    frontmatter = (PLUGIN / f"agents/{agent}.md").read_text().split("---")[1]
    line = next(x for x in frontmatter.splitlines() if x.startswith("tools:"))
    return [t.strip() for t in line.removeprefix("tools:").split(",")]


def test_agents_can_load_the_skill_and_read_its_files():
    for agent in ("iac-pr-worker", "iac-pr-reviewer"):
        assert {"Skill", "Read"} <= set(_tools(agent))
        body = (PLUGIN / f"agents/{agent}.md").read_text().split("---", 2)[2]
        assert "absolute path" in body and "SKILL.md" in body and "review.md" in body
    skill = (PLUGIN / "skills/co-iac-pr/SKILL.md").read_text()
    orchestrate = skill.split("\n## Orchestrate\n")[1].split("\n## Worker\n")[0]
    assert "absolute paths" in orchestrate and "review.md" in orchestrate


def test_reviewer_inputs_agree_everywhere():
    inputs = ("the diff", "the drafted PR body file", "the change note", "change.json", "the worker's validator results")
    for rel in ("skills/co-iac-pr/SKILL.md", "skills/co-iac-pr/review.md", "agents/iac-pr-reviewer.md"):
        text = (PLUGIN / rel).read_text().replace("`", "")
        for item in inputs:
            assert item in text, (rel, item)
        assert "worker report" not in text and "worker's report" not in text, rel

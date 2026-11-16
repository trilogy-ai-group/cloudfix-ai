"""Every `cloudfix ...` command line a skill documents must parse with the real CLI parser."""
import re
import shlex
from pathlib import Path

import pytest

from cloudfix.cli import build_parser

PLUGIN = Path(__file__).resolve().parents[2]
SKILLS = ["co-gp2-to-gp3", "co-gp2-to-gp3-fix"]


def skill_commands(path: Path) -> list[list[str]]:
    """Lines starting with "cloudfix " inside ``` blocks, placeholders replaced by X, without "cloudfix"."""
    commands, in_block = [], False
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("```"):
            in_block = not in_block
        elif in_block and line.startswith("cloudfix "):
            commands.append(shlex.split(re.sub(r"<[^>]+>", "X", line))[1:])
    return commands


@pytest.mark.parametrize("skill", SKILLS)
def test_every_documented_command_parses(skill):
    cmds = skill_commands(PLUGIN / "skills" / skill / "SKILL.md")
    assert cmds, "skill documents no cloudfix commands"
    for argv in cmds:
        build_parser().parse_args(argv)  # raises SystemExit on unknown subcommands/flags


def test_skill_states_the_uv_command_form():
    text = (PLUGIN / "skills/co-gp2-to-gp3/SKILL.md").read_text()
    assert "uv run --frozen --no-dev --project" in text

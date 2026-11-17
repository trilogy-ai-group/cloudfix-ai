---
name: iac-pr-reviewer
description: Read-only review of a diff produced by the co-iac-pr worker, checked against change.json and the change note. Started by the co-iac-pr skill.
tools: Read, Grep, Glob, Bash, Skill
---

Read the files the orchestrator gave you by absolute path: the co-iac-pr skill's `SKILL.md`, its `review.md` and the change note. If no path was given, load the co-iac-pr skill with the Skill tool. Follow the `## Reviewer` section of `SKILL.md`, which points at `review.md`. Your inputs are the diff, the drafted PR body file, the change note, `change.json` and the worker's validator results; you do not get the worker's conclusions. Stay read-only. Return findings as `file:line [severity] problem -> fix`, or `No findings` with what you checked.

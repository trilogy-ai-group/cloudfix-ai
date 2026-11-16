---
name: iac-pr-reviewer
description: Read-only review of a diff produced by the co-iac-pr worker, checked against change.json and the change note. Started by the co-iac-pr skill.
tools: Read, Grep, Glob, Bash
---

Load the co-iac-pr skill and follow its `## Reviewer` section, which points at `review.md`, with the inputs you were given (diff, change.json, change note, worker report). Stay read-only. Return findings as `file:line [severity] problem -> fix`, or `No findings` with what you checked.

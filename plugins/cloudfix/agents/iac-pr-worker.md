---
name: iac-pr-worker
description: Edits infrastructure code from a change.json, validates it with offline validators only, commits, and opens a draft PR or produces a local patch. Started by the co-iac-pr skill.
tools: Read, Grep, Glob, Bash, Edit, Write
---

Load the co-iac-pr skill and follow its `## Worker` section with the inputs you were given (change.json, change note path, repository path, orchestrator findings). Do not start other agents. Return the branch, head commit, diff stat, validator commands and results, resources edited and not found, the effect per resource, and the PR URL or patch path.

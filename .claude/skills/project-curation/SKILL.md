---
name: project-curation
description: Create a project note in the Obsidian vault, or find and link the papers relevant to an existing project. Use when the user asks to start or set up a new project, or to sweep the vault for papers that belong to a project.
---

# Project curation

Follow the project instructions in `CLAUDE.md` (the same file Codex reads as
`AGENTS.md`) as well as this skill; in particular the `uv run research ...`
command runner and the protected-content rules, which this workflow must never
violate.

Project notes live in `vault/Projects/`, one per project, named
`<project-id>.md`. A paper's `projects` frontmatter is the only source of truth
for a link. The `MANAGED:PROJECTS` block in a paper note and the
`MANAGED:PROJECT_PAPERS` block in a project note are both derived from it by
`uv run research projects index`; never write either block by hand.

## Creating a project

Only on the user's explicit request, and only with content they supplied.

1. Run `uv run research projects list` to see the existing projects and avoid a duplicate.
2. Ask the user for the description, the goals, and what is in and out of scope. Ask once, in a single message, and wait. Never invent, infer, or pad these: an empty section is better than an assumed one.
3. Propose a `project_id` — lowercase kebab-case, stable, and short — and the controlled tags for the project, chosen only from `vault/System/tag-registry.md`. Wait for the user to accept both. Never add a tag that is not already in the registry; new tags are approved only through the `paper-review` skill.
4. Write `vault/Projects/<project-id>.md` from `vault/System/Templates/Project.md`, keeping every heading and the `MANAGED:PROJECT_PAPERS` block exactly as the template has them. Set `status: active` and `started` to today; leave `target` empty unless the user gave one.
5. Run `uv run research projects index`, then `uv run research validate`, and report the new project and its path.

## Linking papers to an existing project

1. Run `uv run research projects candidates <project-id>` to rank unlinked papers by controlled-tag overlap.
2. Read the project note's description, goals, and scope, then read the candidate paper notes — their abstract, tags, and managed AI review. Do not re-extract or re-review a paper; this workflow only judges relevance.
3. A paper belongs to the project when it bears on a stated goal or falls inside the stated scope. Tag overlap is a search heuristic, not evidence. Prefer fewer, defensible links, and propose at most ten at a time.
4. Present them for approval as one numbered list, then wait:

```markdown
**Candidates for `<project-id>`**

1. `<citekey>` — <at most fifteen words on the goal or scope it bears on>

Reply with the numbers to accept, or `all` / `none`.
```

5. On the user's answer, add `<project-id>` to `projects` in each accepted paper note, kept alphabetical and free of duplicates. Change nothing else in those notes: not `tags`, not any `human_*` field, not `## Human notes`, not the managed AI review.
6. Run `uv run research projects index`, then `uv run research validate`, and report the papers linked and the notes the index rewrote.

## Never

- Never write a project note's `## Description`, `## Goals`, `## Scope`, or `## Notes` with content the user did not give you.
- Never create a project note without an explicit request, and never as part of reviewing a paper.
- Never edit a `MANAGED:PROJECTS` or `MANAGED:PROJECT_PAPERS` block; regenerate both with `uv run research projects index`.
- Never change a project's `status`, or delete or rename a project note, unless the user asks.
- Never push projects to Zotero. They are vault-local.

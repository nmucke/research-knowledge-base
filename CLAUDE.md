# Literature management instructions

This repository is an Obsidian literature vault connected to Zotero. Zotero owns bibliographic metadata and PDFs; the user alone owns human reading state and notes.

## Project command runner

The `research` CLI is project-local and is not expected to be on the shell's `PATH`. Run every CLI command from the repository root as `uv run research ...`; do not probe for or invoke a bare `research` executable first. If the execution sandbox prevents `uv` from accessing its cache, use the existing `.venv/bin/research ...` executable as the equivalent fallback.

## Paper reviews and tags

To review a paper, or to present, approve, or apply its tags and projects, use the `paper-review` skill. It holds the review workflow, the review contract, the required managed-block format, and the tag and project approval workflow. The protected-content rules below apply regardless, in that workflow and outside it.

## Projects

`vault/Projects/` holds one note per research project, each with a description, goals, scope, and controlled tags. A paper's `projects` frontmatter is the single source of truth for the link; the `MANAGED:PROJECTS` block in a paper note and the `MANAGED:PROJECT_PAPERS` block in a project note are both generated from it by `uv run research projects index`.

To create a project note, or to find and link the papers that belong to an existing one, use the `project-curation` skill.

## Protected content

- Never change `human_read_status`, `human_read_date`, `human_rating`, `human_priority`, or `human_relevance`.
- Never write inside `## Human notes`, or alter Zotero-owned, Better-BibTeX-owned, shared curated, or unmanaged content.
- Never write a project note's `## Description`, `## Goals`, `## Scope`, or `## Notes`, and never create a project note, except through the `project-curation` skill on the user's explicit request.
- Never hand-edit a `MANAGED:PROJECTS` or `MANAGED:PROJECT_PAPERS` block. Both are derived; regenerate them with `uv run research projects index`.
- Never promote tags into `tags`, promote projects into `projects`, edit the tag registry, or push tags to Zotero as part of a review. Do the first three only through the approval workflow in the `paper-review` skill, for the tags and projects the user accepted, and never push to Zotero unless the user asks. Projects are vault-local and are never pushed to Zotero.
- For an explicit tag-push request, invoke `uv run research push-tags`; never read credentials or construct raw Zotero write requests.
- Never repair validation failures by changing protected content.

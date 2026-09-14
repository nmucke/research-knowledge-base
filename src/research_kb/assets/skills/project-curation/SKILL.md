---
name: project-curation
description: Create or update a requested project brief, find papers relevant to its goals or scope, or reassess/archive links. Use for project setup and curation, not broad discovery without a project.
---

# Project curation

Reuse description, goals, scope, identifiers, tags, and authorization already supplied in conversation. Ask once only for consequential missing information. Preserve the user's wording; do not invent research direction.

For creation or an explicit brief update, use `research_projects` to search for duplicates, then create a durable `project-create` or `project-update` proposal with `research_catalog_propose`. User-owned brief changes require an explicit request. Resolved identifier/tag choices need no second approval round.

For links or reassessment, search titles and abstracts as well as tags, follow pagination, and disclose screening coverage. Read summaries first, then full briefs and evidence for plausible papers. Every link rationale must cite a particular goal or scope statement. Do not automatically review papers.

Create durable paper-link proposals with `research_propose_curation`; preview them with `research_preview_proposal`. Preview project creation, brief changes, or archival with `research_catalog_preview`. The user applies paper-link decisions through `research curation decide <id>`, applies catalog proposals through `research catalog decide <id>`, or persists catalog rejection through `research catalog reject <id>`. Never invoke these commands, pass approval booleans, or directly edit links or briefs. Read [references/lifecycle.md](references/lifecycle.md) for scope updates, archival, and reassessment. Never silently unlink, delete a project, or push projects to Zotero.

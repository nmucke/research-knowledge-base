---
name: curation-approval
description: Present and preview durable curation or catalog proposals for user approval. Use for accept, reject, consolidate, project-change, or revisit requests; application remains user-only.
---

# Curation approval

Use `research_list_proposals` to resolve stable identifiers and `research_preview_proposal` to show exact changes, affected records, expected revisions, and prior decisions. List numbers are display shortcuts, never durable records.

For project creation/update/archive or supported tag rename/merge proposals, use `research_catalog_propose` and `research_catalog_preview`. Map acceptance to `research catalog decide <proposal-id>` and rejection to `research catalog reject <proposal-id>` for the user to run. Rejection is durable and clears the proposal from pending views without applying its file diff.

Group decisions into suggested existing tags, new tags, project links, and supported alias/merge/deprecation operations. Map the user's choices to stable decision identifiers and show the exact user command, such as `research curation decide <proposal-id> --accept <item-id> --reject <item-id>`. That command records and applies accepted/rejected decisions atomically; the user, not the agent, runs it. Rejected decisions remain durable.

Never invoke `research curation decide`, `research curation undo`, `research catalog decide`, or `research catalog reject`; pass an `approved` boolean; edit registry/frontmatter directly; or infer application authority from conversation. Give the exact user-facing CLI command returned by preview. Zotero pushes require a separate explicit request; projects are never pushed.

If revisions are stale or an operation unsupported, create a revised proposal or report the limit. Do not approximate aliases, merges, or deprecations manually. Read [references/presentation.md](references/presentation.md) when presenting a proposal.

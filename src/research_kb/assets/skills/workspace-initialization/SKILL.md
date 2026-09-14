---
name: workspace-initialization
description: Guide first-time personalization of an initialized research workspace. Use when the user asks to initialize, configure, personalize, or resume setup; not for routine later curation.
---

# Workspace initialization

Use the research tools when they are available. Call `research_doctor`, `research_setup_status`, and `research_setup_schema`; do not ask the user to run terminal commands or copy output. If setup is partly complete, preserve configured content and gather only missing choices. If it is complete, explain the current setup and route later changes to ordinary workflows. Never reset setup.

Continue offline personalization when doctor reports unavailable Zotero or Better BibTeX. Explain the failed check, but do not treat a missing bibliography or write authorization as a setup blocker. Never request keys in chat or use a Zotero write operation.

Reuse choices already supplied. Ask concise follow-ups only for consequential gaps among interests, goals, methods, experience level, and reading preferences. Offer a small controlled tag vocabulary with definitions and optional starter projects based on user-supplied direction. Read [references/setup-plan.md](references/setup-plan.md) for the request fields and conflict rules.

Ask whether to configure a personal or group Zotero library, or defer it, and whether to run one initial sync after setup. For a new personal library, use `{"library_type":"user","library_id":0}` automatically: the local API selects the current user's library. Do not ask for a numeric Zotero user ID or send the user to account settings. Only a group library needs a positive Zotero group ID; ask for it only if it is not already known. Preserve existing library configuration reported by status. Never request a key in chat. Keep sync as a separate opt-in choice in the displayed plan.

Call `research_setup_preview` with the direct setup request and show its exact changes and deferrals. Once the user accepts that concrete plan, call `research_setup_apply` with the identical request. This is the narrow initial-setup exception to the usual user-only application rule; user acceptance of the displayed plan supplies authorization. Never add an approval boolean, infer consent before acceptance, or extend this authority to curation/catalog decisions.

After apply, call `research_setup_status`, `research_doctor`, and `research_validate`. A completed setup marker does not mean Zotero is connected. If the user already elected initial sync and connectivity is ready, call `research_sync`; it may read local Zotero and create/index/validate paper notes in this workspace. Do not ask again for that sync. Report configured and deferred items and the `vault/` folder to open in Obsidian.

If tools are unavailable in a native terminal agent, use the workspace launcher: `./bin/research doctor`, `./bin/research workspace setup status`, `./bin/research workspace setup schema`, then `preview` and accepted `apply` with `--input setup.json`. Afterward use `./bin/research validate`. For an elected initial sync, run `./bin/research sync`, then `./bin/research projects index`, then `./bin/research validate`. Run commands yourself and do not ask the user to copy output. Do not use this fallback in Cowork.

In Cowork, read [references/cowork-plugin.md](references/cowork-plugin.md). You may use the included `setup-schema.json` to gather preferences and draft a request, but without live status never invent `expected_revision`, claim a preview, or apply changes.

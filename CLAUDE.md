# Literature management instructions

This repository is an Obsidian literature vault connected to Zotero. Zotero owns bibliographic metadata and PDFs; the user alone owns human reading state and notes.

## Project command runner

The `research` CLI is project-local and is not expected to be on the shell's `PATH`. Run every CLI command from the repository root as `uv run research ...`; do not probe for or invoke a bare `research` executable first. If the execution sandbox prevents `uv` from accessing its cache, use the existing `.venv/bin/research ...` executable as the equivalent fallback.

## Paper reviews and tags

To review a paper, or to present, approve, or apply its tags, use the `paper-review` skill. It holds the review workflow, the review contract, the required managed-block format, and the tag approval workflow. The protected-content rules below apply regardless, in that workflow and outside it.

## Protected content

- Never change `human_read_status`, `human_read_date`, `human_rating`, `human_priority`, or `human_relevance`.
- Never write inside `## Human notes`, or alter Zotero-owned, Better-BibTeX-owned, shared curated, or unmanaged content.
- Never promote tags into `tags`, edit the tag registry, or push tags to Zotero as part of a review. Do the first two only through the tag approval workflow in the `paper-review` skill, for the tags the user accepted, and never push to Zotero unless the user asks.
- For an explicit tag-push request, invoke `uv run research push-tags`; never read credentials or construct raw Zotero write requests.
- Never repair validation failures by changing protected content.

# Literature management instructions

This repository is an Obsidian literature vault connected to Zotero.

## Core rules

- Zotero owns bibliographic metadata and PDFs.
- Human notes are owned exclusively by the user.
- Never change `human_read_status`, `human_read_date`, `human_rating`, `human_priority`, or `human_relevance`.
- Never write inside `## Human notes`.
- Update only AI-owned frontmatter fields and the `MANAGED:AI_REVIEW` block.
- Never claim to have read the full paper unless the full extracted text is available.
- Record the actual review scope and extraction coverage.
- Keep AI reviews below 200 words unless explicitly requested otherwise.
- Use at most three summary bullets.
- Include the main contribution, the most important limitations, and a reading recommendation.
- Base recommendations on `System/reading-profile.md`.
- Read `System/tag-registry.md` before assigning tags.
- Prefer existing tags.
- Apply at most five existing tags.
- Suggest at most two new tags.
- Put proposed new tags in `ai_suggested_tags`.
- Do not approve new tags automatically.
- Do not push tags to Zotero unless explicitly asked.
- Run `research validate <citekey>` after editing a paper note.
- If extraction is incomplete, say so explicitly.
- Do not treat an abstract as evidence for details found only in the body.

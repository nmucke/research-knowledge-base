# Research Knowledge Base

A local-first literature-management vault connecting Zotero, Better BibTeX,
Obsidian, and a small Python CLI. The implementation follows
`specs_document.pdf` and currently contains the repository and core CLI workflow.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended), or `pip`

## Development setup

With `uv`:

```sh
uv sync --all-groups
uv run research --help
uv run pytest
```

Alternatively, create a Python 3.11 virtual environment and run:

```sh
python -m pip install -e .
research --help
```

Copy `.env.example` to `.env` for local overrides. Never commit `.env` or
anything containing Zotero credentials.

## Diagnostics

Implementation steps 1 through 11 are complete. Run the local environment checks
with:

```sh
uv run research doctor
```

The command checks Zotero and Better BibTeX connectivity, required vault paths,
`references.bib`, configuration, and local-write credential status. It runs all
checks, exits non-zero when a required check fails, and treats optional setup as
a warning.

## Inspect an item

Display the Zotero metadata and Better BibTeX citation key for a local item:

```sh
uv run research show --zotero-key ABCD1234
```

## Synchronize notes

Import Zotero changes into paper notes with:

```sh
uv run research sync
```

Use `--dry-run` to preview changes, `--full` to ignore the saved cursor, or
target one item with `--item ABCD1234` or `--citekey chen2025flowdas`.
Normal runs use the cursor in `.research/sync-state.json` to fetch only changed
items. If that incremental request cannot be used (including when Zotero's
server identity changes), the command automatically falls back to a full sync
and reports that choice. Sync updates only Zotero-owned metadata and preserves
your human notes. Use `--full` after permanently emptying Zotero's trash when
the installed local API does not expose its deletion log.

## Extract PDF text

After synchronizing an item with a locally available PDF, extract page-aware text
with:

```sh
uv run research extract chen2025flowdas
```

The generated cache lives at `.research/paper-text/<citekey>.md` and includes
page markers plus source and extractor provenance. Repeated runs reuse a valid
cache; `--force` rebuilds it. The command reports empty pages, text volume, page
failures, and likely scanned PDFs. OCR is intentionally outside version 0.1.

To prepare the inputs for a Claude Code or Codex review, run:

```sh
uv run research review-context chen2025flowdas
```

This refreshes extraction when needed and prints the paper note, extracted text,
reading profile, and tag registry paths. `CLAUDE.md` and `AGENTS.md` contain the
same constrained review contract; `System/Templates/Paper.md` is the canonical
paper-note template. The command also stores a one-shot snapshot of human-owned
fields and a hash of the Human notes section. A successful targeted validation
consumes that snapshot; a failure retains it so protected changes can be corrected.

## Validate paper notes

Validate every paper note in the vault with:

```sh
uv run research validate
```

Pass a citation key to validate one note:

```sh
uv run research validate chen2025flowdas
```

Validation checks the paper schema, status values, managed blocks, human/AI
ownership rules, review completeness and provenance, extraction consistency,
and controlled tags. It reports all discovered issues; validation errors produce
a nonzero exit status, while warnings do not.

Unknown tags are errors by default. To report unknown tags as warnings instead,
set this local override in `.env`:

```dotenv
UNKNOWN_TAG_POLICY=warning
```

This policy changes only the severity of unknown tags. It does not approve,
promote, or push tags to Zotero.

## Reconcile approved tags with Zotero

Only tags already approved in a paper note's `tags` frontmatter are eligible to
be sent to Zotero. AI-suggested tags are never pushed. To approve a suggestion,
first add it to `System/tag-registry.md`, move it from `ai_suggested_tags` into
`tags`, and remove it from `ai_suggested_tags`. Inspect the deterministic local
comparison first:

```sh
uv run research tags chen2025flowdas
uv run research push-tags chen2025flowdas --dry-run
```

After reviewing that output, configure write access. Current stable Zotero
builds expose a read-only local API, so create a dedicated key with library
write permission at <https://www.zotero.org/settings/keys> and add its key and
numeric user or group library ID to the ignored `.env` file:

```dotenv
ZOTERO_WEB_API_KEY=<dedicated-write-key>
ZOTERO_WEB_LIBRARY_ID=<numeric-library-id>
```

The numeric user ID is shown on Zotero's API Keys page. Then verify the
configured key without printing or copying it:

```sh
uv run research authorize
```

If a future Zotero build provides local write authorization, the same command
uses it preferentially and stores its server-scoped credential in
`.research/credentials.json`. Both `.env` and the local credential file are
ignored by Git, and keys are never printed or logged. Then perform the
deliberate, add-only write:

```sh
uv run research push-tags chen2025flowdas
```

Tag pushes add missing approved tags and preserve every existing Zotero tag;
they never remove tags. On a version conflict, the command fetches the newest
item and retries once with a newly merged tag list. A second conflict stops
without updating the note; inspect with `uv run research tags <citekey>` before
retrying.
For multiple notes, only `push-tags --all --dry-run` is supported, so each live
write remains an explicit citation-key action. Local writes are preferred when
available; otherwise the command uses only the explicitly configured official
Zotero Web API fallback.

By default the allowed controlled-tag namespaces are `domain`, `method`,
`task`, `property`, `model`, and `data`. A local comma-separated subset may be
configured with `ALLOWED_TAG_NAMESPACES`; this does not approve new tags.

## Obsidian dashboards

The `.base` files in `Literature/Dashboards/` are Obsidian Bases views that
query paper-note frontmatter in `Literature/Papers/`; simply opening a
dashboard does not change any notes. Enable the core **Bases** feature in
Obsidian's Settings, then open a `.base` file from the File Explorer.

| Dashboard | Purpose |
| --- | --- |
| `Inbox.base` | Unread papers awaiting triage or review. |
| `Reading Queue.base` | Queued papers, ordered by human priority and relevance. |
| `AI Reviewed.base` | Papers with an AI review and its recommendation details. |
| `Recommended Reading.base` | Unread or queued papers recommended as `must-read` or `read`. |
| `Human Read.base` | Contains **Read** and **Read but AI-unverified** views: completed papers, and completed papers whose AI review still needs human verification. |

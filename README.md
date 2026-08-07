# Research Knowledge Base

A local-first literature-management vault connecting Zotero, Better BibTeX,
Obsidian, and a small Python CLI, implemented from `specs_document.pdf`.

Zotero owns bibliographic metadata, PDFs, annotations, and its own tags. The
Markdown vault owns your reading status, your notes, the AI review state, and
curated tags. The `research` CLI moves data between the two conservatively:
it never overwrites human notes, never deletes Zotero tags, and only writes to
Zotero when you explicitly ask it to.

Claude Code and Codex read the extracted paper text and write only AI-owned
fields and the managed AI-review block; they follow `CLAUDE.md` and `AGENTS.md`.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended), or `pip`
- Zotero 7 with its local API enabled
- The Better BibTeX for Zotero extension
- Obsidian (optional, but the intended reading and triage interface)

## Setup from a clean checkout

### 1. Install the CLI

With `uv`:

```sh
uv sync --all-groups
uv run research --help
```

Alternatively, create a Python 3.11 virtual environment and run:

```sh
python -m pip install -e .
research --help
```

The `research` command is project-local. Every example below uses
`uv run research ...` from the repository root.

### 2. Enable Zotero's local API

Start Zotero and enable the local HTTP API in Zotero's *Advanced* settings
(the setting that allows other applications on this computer to communicate
with Zotero). The API then answers on `http://localhost:23119/api/`.

The local API is unauthenticated for reads, so keep the port bound to
localhost and never forward it to other machines.

### 3. Install Better BibTeX

Install Better BibTeX in Zotero and let it generate citation keys. Pin the keys
of papers you already cite elsewhere, so a metadata edit cannot silently rename
a note. Configure a "Keep updated" export of your library to `references.bib`
in this repository if you want a live bibliography.

Better BibTeX answers JSON-RPC on
`http://localhost:23119/better-bibtex/json-rpc`.

### 4. Configure local settings

```sh
cp .env.example .env
```

The defaults match a standard single-user Zotero installation
(`ZOTERO_LIBRARY_TYPE=user`, `ZOTERO_LIBRARY_ID=0`). Set a group library ID if
you work in a group library. `.env`, `.research/credentials.json`, and every
API key are ignored by Git and must never be committed.

### 5. Open the vault in Obsidian

Open the `vault/` folder — not the repository root — as an Obsidian vault. The
vault contains only literature content, so the CLI source, tests, generated
state, and project documentation stay out of the Obsidian interface:

```
research-knowledge-base/     project root (run the CLI here)
├── vault/                   ← open this folder in Obsidian
│   ├── Literature/
│   │   ├── Papers/          one Markdown note per paper
│   │   └── Dashboards/      Obsidian Bases views
│   └── System/
│       ├── Templates/
│       ├── reading-profile.md
│       └── tag-registry.md
├── .research/               generated, disposable state
├── src/, tests/             the research CLI
└── references.bib           Better BibTeX keep-updated export
```

Enable the core **Bases** feature to use the dashboards in
`vault/Literature/Dashboards/`. No Zotero-specific Obsidian plugin is required.

`RESEARCH_OBSIDIAN_DIR` sets the vault folder name, relative to the project
root; the default is `vault`. Rename the folder and the setting together if you
prefer a different name.

### 6. Verify the installation

```sh
uv run research doctor
```

The command checks Zotero and Better BibTeX connectivity, required vault paths,
`references.bib`, configuration, and local-write credential status. It runs all
checks, exits non-zero when a required check fails, and treats optional setup as
a warning. Missing write credentials are a warning: they are only needed for
tag pushes.

## The literature workflow

1. Save a paper with the Zotero Connector and confirm Zotero has metadata and a
   PDF.
2. Run `uv run research sync` to create or update the paper note.
3. Open the note in Obsidian and optionally set `human_read_status: queued`,
   `human_priority`, and `human_relevance`.
4. Run `uv run research review-context <citekey>` and ask Claude Code or Codex
   to review the paper.
5. Run `uv run research validate <citekey>` until it passes.
6. Approve tags: the agent lists its applied and suggested tags as a numbered
   list, you reply with the numbers to accept, and it moves those tags into
   `tags`, defines each accepted suggestion in `vault/System/tag-registry.md`,
   and clears the ruled-on entries from `ai_suggested_tags`. You can also do
   this by hand; nothing is approved until it appears in `tags`.
7. Run `uv run research push-tags <citekey> --dry-run`, then
   `uv run research push-tags <citekey>`.
8. Read the paper yourself, set `human_read_status: read` with `human_read_date`
   and `human_rating`, and write under `## Human notes`. The AI review stays
   unchanged next to your own assessment.

`tests/integration/test_acceptance.py` drives this entire sequence, including a
changed PDF marking the review outdated, against a fake Zotero API.

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

When a reviewed paper's PDF attachment changes under a text-based review, or its
title or abstract changes under an abstract-only review, sync sets
`ai_review_status: outdated` so the review can be repeated deliberately.

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
same constrained review contract; `vault/System/Templates/Paper.md` is the
canonical paper-note template. The command also stores a one-shot snapshot of human-owned
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
be sent to Zotero. AI-suggested tags are never pushed. Approving a suggestion
means defining it in `vault/System/tag-registry.md`, moving it from
`ai_suggested_tags` into `tags`, and removing it from `ai_suggested_tags` —
either yourself or through the agent's tag approval workflow in `AGENTS.md`,
which acts only on the tags you accept. Inspect the deterministic local
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

The `.base` files in `vault/Literature/Dashboards/` are Obsidian Bases views that
query paper-note frontmatter in `vault/Literature/Papers/`; simply opening a
dashboard does not change any notes. Enable the core **Bases** feature in
Obsidian's Settings, then open a `.base` file from the File Explorer.

| Dashboard | Purpose |
| --- | --- |
| `Inbox.base` | Unread papers awaiting triage or review. |
| `Reading Queue.base` | Queued papers, ordered by human priority and relevance. |
| `AI Reviewed.base` | Papers with an AI review and its recommendation details. |
| `Recommended Reading.base` | Unread or queued papers recommended as `must-read` or `read`. |
| `Human Read.base` | Contains **Read** and **Read but AI-unverified** views: completed papers, and completed papers whose AI review still needs human verification. |

## Generated state

Everything under `.research/` is rebuildable and normally excluded from Git:
extracted paper text, the sync cursor, review snapshots, backups, logs, and any
local credential. Deleting the directory costs nothing but a re-extraction; the
paper notes themselves are the durable record.

Command logs are written to `.research/logs/`. They record commands, item keys,
files changed, and tags added, and they never contain API keys or paper text.
Use `--verbose` for detailed console diagnostics.

## Troubleshooting

Run `uv run research doctor` first: it identifies most setup problems, and its
output distinguishes a failed required check from an optional warning.

**`Zotero local API is unavailable`** — Zotero is not running, or its local API
is not enabled in Zotero's Advanced settings. Confirm the endpoint answers:
`curl -i http://localhost:23119/api/`. If you changed the port, set
`ZOTERO_LOCAL_API` in `.env`.

**`Better BibTeX is unavailable`** — the extension is missing or Zotero was not
restarted after installing it. Confirm that
`http://localhost:23119/better-bibtex/json-rpc` responds.

**`Better BibTeX has no citation key for Zotero item ...`** — generate or
refresh the key in Better BibTeX (right-click the item), then run `sync` again.

**A note was renamed unexpectedly** — the citation key changed in Better BibTeX.
Sync renames the file and preserves its contents. Pin citation keys in Better
BibTeX for papers you cite elsewhere.

**`Paper ... has no PDF attachment`** — attach a PDF in Zotero and run
`uv run research sync` before extracting. Linked files must exist locally;
PDFs are never copied into the vault.

**`contains no usable extractable text`, or "The PDF appears scanned"** — the
PDF has no text layer. OCR is outside version 0.1; review from the abstract
instead and record `ai_review_scope: abstract-only`.

**Extraction seems stale** — rebuild the cache with
`uv run research extract <citekey> --force`.

**`full-text-cache-missing` or `extraction-cache-mismatch`** — the review claims
full-text coverage but the cache is absent or belongs to a different PDF. Run
`uv run research extract <citekey>` and repeat the review; a replaced PDF also
sets `ai_review_status: outdated`.

**`human-field-changed` or `human-notes-changed`** — an agent modified protected
content during a review workflow. Restore the human values (Git history is the
safety net) and re-run `uv run research validate <citekey>`. Never repair these
by editing the snapshot.

**`tag-unknown` or `applied-tag-unknown`** — a tag is missing from
`vault/System/tag-registry.md`. Add a definition there if you approve the tag, or
remove it from the note. Suggested tags must stay outside `tags` until you
approve them.

**`No local Zotero authorization is stored`** — run `uv run research authorize`.
If Zotero reports that local writes are unsupported, configure
`ZOTERO_WEB_API_KEY` and `ZOTERO_WEB_LIBRARY_ID` in `.env` and run `authorize`
again to verify the key.

**`Zotero item changed before the tag update could be applied`** — the item was
edited in Zotero during the push. The command already retried once; run
`uv run research tags <citekey>` to inspect the current state and push again.

**Notes marked `zotero_missing: true`** — the item is no longer in the Zotero
library the CLI reads. Nothing is deleted automatically. Restore the item in
Zotero, or delete the note yourself once you are sure.

**Incremental sync missed a deletion** — some Zotero builds do not expose the
deletion log. Run `uv run research sync --full` to reconcile.

## Development

```sh
uv run ruff check .
uv run mypy
uv run pytest
```

Unit tests live in `tests/unit/`; `tests/integration/` holds the end-to-end
acceptance test for the complete workflow. CI runs the same three commands on
every push and pull request.

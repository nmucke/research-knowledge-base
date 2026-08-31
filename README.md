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
Two skills carry the workflows: `paper-review` reviews a paper and proposes tags
and projects, `project-curation` creates a project or finds its papers. Neither
approves anything; you do.

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
│   ├── Projects/            one Markdown note per project, plus dashboard.base
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

Steps 4–6 happen inside Claude Code or Codex through the `paper-review` skill.
The CLI commands named there are the agent's, not yours.

1. Save a paper with the Zotero Connector and confirm Zotero has metadata and a
   PDF.
2. Run `uv run research sync` to create or update the paper note.
3. Open the note in Obsidian and optionally set `human_read_status: queued`,
   `human_priority`, and `human_relevance`.
4. Ask the agent to **review `<citekey>`**. The skill runs `review-context`,
   reads the extracted text, your reading profile, the tag registry, and every
   active project note, writes the AI-owned frontmatter and the managed
   AI-review block, and runs `validate` until it passes. It never touches your
   reading state or `## Human notes`.
5. Approve what it proposes. The agent ends with one numbered list: applied
   tags, suggested tags, and the projects it judged the paper relevant to.
   Reply with the numbers you accept, or `all` / `none`.
6. The agent promotes only what you accepted into `tags` and `projects`, defines
   each accepted tag in `vault/System/tag-registry.md`, clears the ruled-on
   suggestions, and runs `projects index`. Nothing is approved until it appears
   in `tags` or `projects`.
7. Run `uv run research push-tags <citekey> --dry-run`, then
   `uv run research push-tags <citekey>`. Tags reach Zotero only on this
   explicit request; projects never do.
8. Read the paper yourself, set `human_read_status: read` with `human_read_date`
   and `human_rating`, and write under `## Human notes`. The AI review stays
   unchanged next to your own assessment.

`tests/integration/test_acceptance.py` drives this entire sequence, including a
changed PDF marking the review outdated, against a fake Zotero API.

## The project workflow

Projects are created and populated through the `project-curation` skill. See
[Projects](#projects) for the file layout and the derived links.

1. Ask the agent to **start a project**. It runs `projects list` to avoid a
   duplicate, then asks you once for the description, goals, and what is in and
   out of scope. It writes nothing you did not say; an empty section is better
   than an assumed one.
2. Approve the `project_id` and tags it proposes. The identifier becomes the
   filename; the tags must already exist in `vault/System/tag-registry.md`.
3. The agent writes `vault/Projects/<project-id>.md` from the template, runs
   `projects index` and `validate`, and reports the path.
4. From then on every `paper-review` weighs the paper against each active
   project and offers the relevant ones for approval, as step 5 above.
5. To back-fill papers reviewed before the project existed, ask the agent to
   **find papers for `<project-id>`**. It runs `projects candidates`, reads the
   ranked notes, and proposes at most ten as one numbered list. Tag overlap
   decides only where it looks; your stated goals and scope decide what it
   proposes.
6. Approve by number. The agent adds the project to each accepted paper's
   `projects`, runs `projects index`, and validates.

You own the brief. The agent never writes a project's description, goals, or
scope with content you did not give it, never creates a project note unprompted,
and never promotes a paper into `projects` without your approval.

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
reading profile, tag registry, and active project-note paths. `CLAUDE.md` and `AGENTS.md` contain the
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
controlled tags, and project references. A whole-vault run also validates the
project notes themselves and reports derived project links that are out of date. It reports all discovered issues; validation errors produce
a nonzero exit status, while warnings do not.

Unknown tags are errors by default. To report unknown tags as warnings instead,
set this local override in `.env`:

```dotenv
UNKNOWN_TAG_POLICY=warning
```

This policy changes only the severity of unknown tags. It does not approve,
promote, or push tags to Zotero.

## Projects

`vault/Projects/` holds one note per research project — a description, goals,
scope, and controlled tags from the same registry the papers use. Create one
from `vault/System/Templates/Project.md`, named after its `project_id`.

A paper's `projects` frontmatter is the single source of truth for a link. Two
managed blocks are derived from it: `MANAGED:PROJECTS` in the paper note and
`MANAGED:PROJECT_PAPERS` in the project note. Never edit either by hand.

```sh
uv run research projects index              # rebuild both after any projects change
uv run research projects index --dry-run    # report stale links without writing
uv run research projects list               # projects and their link counts
uv run research projects candidates <id>    # rank unlinked papers by tag overlap
```

`validate` reports an out-of-date block as `project-index-stale`. The paper-note
block appears the first time a paper is linked, so unlinked notes stay clean.

Projects are vault-local and are never pushed to Zotero. Open
`vault/Projects/dashboard.base` in Obsidian for three views: every project, every
linked paper, and reviewed papers not yet linked to one.

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

`vault/Projects/dashboard.base` is a separate Bases file next to the project
notes; see [Projects](#projects).

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

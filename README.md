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
paper-note template.

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
promote, or push tags to Zotero. Agent workflow acceptance testing and tag
dry-run support follow in implementation steps 12 through 14.

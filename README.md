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

Implementation steps 1 through 4 are complete. Run the local environment checks
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

PDF resolution and synchronization are not part of the implemented workflow.

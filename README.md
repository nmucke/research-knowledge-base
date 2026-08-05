# Research Knowledge Base

A local-first literature-management vault connecting Zotero, Better BibTeX,
Obsidian, and a small Python CLI. The implementation follows
`specs_document.pdf` and currently contains the repository and CLI foundation.

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

## Current implementation status

Implementation order is defined in section 30 of the specification. Step 1 is
the repository and CLI skeleton. The `research doctor` command is registered as
the boundary for step 2 and currently exits with an explicit not-implemented
message.

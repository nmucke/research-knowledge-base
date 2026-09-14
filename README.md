# Research Knowledge Base

[![CI](https://github.com/nmucke/research-knowledge-base/actions/workflows/test.yml/badge.svg)](https://github.com/nmucke/research-knowledge-base/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Platforms: macOS and Linux](https://img.shields.io/badge/platforms-macOS%20%7C%20Linux-lightgrey)](#get-started)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[![Managed with uv](https://img.shields.io/badge/managed%20with-uv-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![Linting: Ruff](https://img.shields.io/badge/linting-Ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Types: mypy](https://img.shields.io/badge/types-mypy-2A6DB2)](https://mypy.readthedocs.io/)
[![MCP: local stdio](https://img.shields.io/badge/MCP-local%20stdio-111111)](#skills-and-mcp)
[![Zotero](https://img.shields.io/badge/Zotero-CC2936?logo=zotero&logoColor=white)](https://www.zotero.org/)
[![Obsidian](https://img.shields.io/badge/Obsidian-7C3AED?logo=obsidian&logoColor=white)](https://obsidian.md/)

Use Claude or ChatGPT/Codex to review your Zotero library, organize projects, and
synthesize research. Read and annotate the results in Obsidian.

## Get started

### 1. Install and create your library

You need **macOS or Linux**, **Python 3.11+**, [uv](https://docs.astral.sh/uv/),
Zotero, [Better BibTeX](https://retorque.re/zotero-better-bibtex/installation/),
and one of the agent clients below. Obsidian is optional.

In Zotero, enable **Settings → Advanced → Allow other applications on this
computer to communicate with Zotero**, then leave Zotero running.
See [Zotero's local API instructions](https://www.zotero.org/support/dev/web_api/v3/local_api).

```sh
git clone https://github.com/nmucke/research-knowledge-base.git
cd research-knowledge-base
uv sync --all-groups --locked
uv run research workspace init ~/Research/my-library
uv run research --workspace ~/Research/my-library doctor
```

Choose any empty workspace directory. `workspace init` automatically creates the
agent instructions, research skills, setup schema, launcher, MCP configuration,
and Cowork plugin for that exact directory. No personal path needs to be added
to the repository, and new workspaces need no separate refresh command.

Fix any Zotero/Better BibTeX connection failures before importing papers. You can
personalize the workspace offline; missing bibliography or write-authorization
warnings do not prevent setup.

### 2. Connect your agent

Open **the research workspace** (`~/Research/my-library`) in your agent.

#### Claude Cowork

1. Open Claude Desktop's **Cowork** tab.
2. Choose **Customize → Plugins**, then upload a custom plugin. Select
   `~/Research/my-library/.research-tools/research-workspace.plugin.zip`.
3. Start a Cowork task with `~/Research/my-library` as its working folder.
4. Prompt: **“Initialize this research workspace.”**

The plugin is generated automatically; installing/enabling it in Cowork is a
one-time client step. It runs the MCP server on your computer, where Zotero and
the Python environment are available. Cowork's isolated shell cannot run the
Mac launcher. Organization settings may restrict plugin installation.
See [Claude's plugin guide](https://support.claude.com/en/articles/13837440-use-plugins-in-claude).

#### Claude Code

With Claude Code installed and signed in:

```sh
cd ~/Research/my-library
claude
```

Approve the workspace's `research` MCP server when prompted, check its connection
with `/mcp`, then prompt **“Initialize this research workspace.”** The generated
`.mcp.json` and `.claude/skills/` supply the tools and workflows.
See [Claude Code MCP setup](https://code.claude.com/docs/en/mcp).

#### ChatGPT/Codex desktop and Codex CLI

In the desktop app, open `~/Research/my-library` as a local folder and choose
**Codex** or **ChatGPT Work**. Alternatively, with the Codex CLI installed and signed in:

```sh
cd ~/Research/my-library
codex
```

Trust the workspace when prompted so its `.codex/config.toml` can load. Check
`/mcp` for the `research` server, then prompt **“Initialize this research
workspace.”** Instructions and skills are already installed in the workspace.
See the [desktop guide](https://learn.chatgpt.com/docs/app) and
[local MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

If your desktop session does not load the project configuration, use
**Settings → MCP servers → Add server**: choose **STDIO**, name it `research`,
set the command to the **absolute path** of `my-library/bin/research`, and set
its argument to `mcp`. Save and restart the connection.

**ChatGPT in a browser:** this repository currently supplies a local stdio
server. Browser developer-mode apps use remote HTTP/SSE servers, so they require
a separate deployment that this repository does not provide. Use a local client
for this setup. See [ChatGPT developer mode](https://developers.openai.com/api/docs/guides/developer-mode).

### 3. Personalize and start researching

The agent asks about your interests, reading preferences, desired tags, initial
projects, Zotero library, and whether to import papers. It previews the choices
and applies the initial setup after you accept them. Interrupted setup can resume.
Personal libraries use local user ID `0` automatically; you do not need to look
up your Zotero account ID or provide an API key. Group libraries need their group ID.

Open **`~/Research/my-library/vault/`** in Obsidian. Keep Zotero open for sync and
PDF retrieval. Then try:

- “Sync my Zotero library.”
- “Review `CITEKEY` and recommend whether I should read it.”
- “Compare these papers on uncertainty quantification.”
- “Find papers relevant to my project and propose links.”

Reviews preserve your notes and reading state. Later tag and project changes
become proposals; the agent provides the exact commands for you to apply your
decisions. Initial setup is the only agent-applied curation exception.

## Skills and MCP

**Skills describe the workflow; MCP tools perform validated operations.** Both
are installed automatically when a workspace is created.

| Skill | Purpose |
| --- | --- |
| `workspace-initialization` | Set up interests, tags, projects, and optional first sync. |
| `paper-review` | Review a paper using available evidence and state coverage limits. |
| `project-curation` | Draft project briefs and propose relevant paper links. |
| `curation-approval` | Present proposals and exact user decision commands. |
| `research-synthesis` | Produce cited comparisons and answers across papers. |
| `literature-discovery` | Build deduplicated shortlists; external search needs client search tools. |

The MCP server exposes setup, diagnostics, sync, search, paper/project retrieval,
paginated evidence, review submission, proposals, and separate analysis artifacts.
Writes use validation and revision checks. Tools cannot execute arbitrary shell
commands, expose credentials, apply recurring curation decisions, undo changes,
or push to Zotero. Client-granted filesystem permissions remain separate.

The CLI uses the same services. From the research workspace:

```sh
./bin/research doctor
./bin/research search "inverse problems" --limit 10
./bin/research validate
./bin/research curation list
./bin/research api schema
./bin/research --help
```

For another stdio-capable client, configure the absolute `bin/research` path as
the command and `mcp` as its argument. Claude Desktop **Chat** also has a generated
`claude-desktop-mcp.json` configuration fragment; that fragment does not configure
Cowork.

## Repository and data

The repository contains reusable software and neutral starter assets. Your
workspace contains private research data. Markdown is the durable record;
no database is required.

```text
research-knowledge-base/       software checkout
  src/research_kb/             services, CLI, MCP, packaged research skills
  tests/                      synthetic fixtures and integration tests
  .agents/skills/              development-only workflow evaluation

~/Research/my-library/         private research workspace
  AGENTS.md, CLAUDE.md         research-agent instructions
  bin/research                workspace-bound launcher
  vault/                      Obsidian papers, analyses, projects, dashboards
  .research/                  proposals, provenance, history, recovery
  .cache/research-kb/          disposable extracted-text cache
  .env, references.bib         local configuration, optional bibliography
```

Zotero owns bibliography and PDFs. You own reading state, human notes, project
briefs, and approval decisions. Back up the workspace, including `.research/`;
only the cache is disposable. OCR is not implemented.

The launcher uses the Python environment that created it. Keep the checkout and
its `.venv` available. Optional overrides belong in the workspace's `.env`; see
[.env.example](.env.example). `--workspace` selects that directory's configuration.
Zotero write credentials are needed only for explicit tag pushes, never for
ordinary local reads or initialization.

### Update or migrate

After updating the software, refresh an existing workspace's generated support:

```sh
# From the software checkout
uv sync --all-groups --locked
uv run research workspace refresh-agent-files ~/Research/my-library
```

Refresh preserves research content, backs up replaced support files, and
preserves unrelated MCP entries. Restart your agent; Cowork users should upload
the regenerated plugin. This is an upgrade step, not part of creating a new library.

To migrate an older mixed code-and-data repository:

```sh
uv run research workspace migrate /path/to/legacy-repo ~/Research/my-library
uv run research --workspace ~/Research/my-library validate
```

Migration requires an empty destination, verifies the copy, and leaves the source
intact. Back up and validate before removing any originals.

### Share and develop

To share source without private workspace data or Git history:

```sh
uv run research workspace export-source /tmp/research-kb-public
uv run research workspace audit /tmp/research-kb-public --public-source
```

Old Git history may still contain private data; deleting files does not remove it.
Development agents follow checkout `AGENTS.md` and use synthetic fixtures.
Research agents work in separate workspaces with the installed research skills.

```sh
uv run ruff check .
uv run mypy
uv run pytest
uv build --wheel --sdist
```

Tests cover ownership preservation, stale revisions, recovery, migration, public
exports, and real MCP/JSON interfaces. Database work remains deferred.

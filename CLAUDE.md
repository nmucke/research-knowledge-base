# Development checkout instructions

This repository contains distributable `research-kb` software. Work here as a software-development agent. Personal literature, projects, reading profiles, credentials, and live Zotero state belong in separate research workspaces.

## Commands and architecture

- Run project commands from the repository root with `uv run ...`. The application CLI is `uv run research ...`; if sandboxed cache access prevents `uv`, use `.venv/bin/research ...`.
- Keep policy and validation in shared Python services. CLI JSON commands and MCP tools are adapters over those services and must behave equivalently.
- Treat Markdown and Zotero as authoritative where the models specify. Search indexes are rebuildable derived state.
- Put schema changes behind migrations and test realistic upgrades.

## Test data and safety

- Use only synthetic fixtures and temporary workspaces. Never use a personal vault, live Zotero library, `.env`, credentials, or copied research state.
- Check protected-content preservation and stale revisions for mutations. Never weaken validation or rewrite protected fields to pass tests.

## Skills

Use the development-only `workflow-evaluation` skill for requested evaluations of agent workflows or substantial review/approval changes. Research skills are packaged under `src/research_kb/assets/skills/` and installed into research workspaces; they are intentionally not discoverable here.

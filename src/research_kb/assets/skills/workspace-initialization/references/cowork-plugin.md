# Claude Cowork plugin connection

Claude Cowork code execution runs in an isolated environment and cannot use a macOS workspace `.venv`. Do not try `./bin/research`, ask for source access, use “Let Claude use your computer,” configure a chat-only connector, or ask the user to paste terminal output.

The initialized workspace includes `.research-tools/research-workspace.plugin.zip`, which bundles this skill and a host-run local MCP server bound to this workspace. Ask the user to install that exact file:

1. Open the **Cowork** tab in Claude.
2. Open **Customize** in the left sidebar, then **Plugins**.
3. Choose the option to upload a custom plugin and select `.research-tools/research-workspace.plugin.zip` from this workspace.
4. Return to the task and retry `research_setup_status`.

If an older initialized workspace does not contain the plugin, the user can refresh agent support from the software checkout with `uv run research workspace refresh-agent-files /absolute/path/to/workspace`, then upload the generated file. This refresh preserves research content. Give the command once; do not enter a loop asking the user to copy command output.

Plugins can be unavailable when an organization administrator disables local MCP servers. If installation is unavailable or the tool still does not appear, explain that limitation and stop at a draft based on `setup-schema.json`; never invent `expected_revision`, a preview, or application. The workspace `.mcp.json` is for Claude Code. `claude-desktop-mcp.json` is a manual Chat configuration fragment; neither configures Cowork.

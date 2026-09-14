# Setup plan

Use `research_setup_schema`; do not guess fields. If research tools are unavailable, the included root `setup-schema.json` may guide a draft only. Start an actionable request only from the revision returned by `research_setup_status`:

Tag namespaces are `domain`, `method`, `task`, `property`, `model`, and `data`; names use lowercase kebab-case after the slash. Respect any workspace restriction reported by validation. Suggest a few useful tags rather than filling every namespace.

```json
{
  "expected_revision": "<status.revision>",
  "reading_profile": {
    "primary_interests": "<interests, goals, methods, and level>",
    "valuable_papers": "<what makes a paper valuable>",
    "lower_priority_papers": "<what to deprioritize>",
    "recommendation_policy": "<reading and recommendation preferences>"
  },
  "tags": [{"name": "domain/example", "definition": "Use when ..."}],
  "projects": [{
    "operation": "project-create",
    "project_id": "example-project",
    "title": "Example project",
    "description": "<user-supplied description>",
    "goals": ["<goal>"],
    "scope_in": ["<included work>"],
    "scope_out": ["<excluded work>"],
    "tags": ["domain/example"],
    "rationale": "Initial project requested during workspace setup."
  }],
  "library": {"library_type": "user", "library_id": 0}
}
```

All four sections are optional. Use `null` for a deferred `reading_profile` or `library`, and use `[]` or omit the field for deferred `tags` or `projects`.

For a new personal library, send `{"library_type":"user","library_id":0}`; `library_id` may also be omitted because it defaults to `0`. No account ID lookup or API key is needed. Zotero's local API accepts `0` for the current user's library. For a group library, send `{"library_type":"group","library_id":<positive group ID>}`; a group ID is not a user ID. Reuse existing configuration from status. Setup writes library configuration only when it is absent; differing existing values require a user-local edit. See [Zotero's local API](https://www.zotero.org/support/dev/web_api/v3/local_api).

Status reports completion, revision, customized profile text, existing tags/projects, library identity, and paper count. Before apply, summarize the exact profile, every tag and definition, every project brief, configured content that will be skipped, and the separate sync choice. Approval covers only that displayed revision and plan. Apply is idempotent for an exact replay; changed plans after completion are rejected. On a customized profile, existing project, contradictory tag definition, library conflict, or stale revision, preserve the existing value and revise or defer the plan; never force replacement.

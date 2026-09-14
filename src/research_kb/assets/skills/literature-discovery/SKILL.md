---
name: literature-discovery
description: Find papers on a topic or work missing from a project, producing a ranked dated shortlist. Use for local or external searches; discovery does not authorize imports, reviews, or links.
---

# Literature discovery

Translate the request into concepts, synonyms, identifiers, constraints, and inclusion criteria. Search locally with `research_search` across titles, abstracts, and tags; follow pagination and retain ranking explanations. For project gaps, retrieve project context and compare candidates with stated goals/scope.

Use external scholarly sources when work beyond the library is requested. Deduplicate by DOI, arXiv ID, then normalized bibliographic identity. Prefer primary bibliographic records and source links. Label local matches, external candidates, and metadata-only assessments.

Read [references/search-record.md](references/search-record.md). Rank by relevance with a reason and evidence limit per item. Save a requested dated record with `research_save_artifact(kind="discovery")`. Discovery never imports, reviews, links, or creates curation proposals.

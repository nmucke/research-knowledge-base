"""Strict parser for the vault's controlled tag registry."""

from __future__ import annotations

import re
from pathlib import Path

from research_kb.exceptions import ValidationError
from research_kb.models import (
    TAG_NAMESPACES,
    TagRegistry,
)
from research_kb.models import (
    TagRegistryEntry as RegistryEntry,
)

__all__ = ("TAG_NAMESPACES", "RegistryEntry", "TagRegistry", "parse_tag_registry")

_TAG_HEADING = re.compile(r"^### `([^`]+)`\s*$")
_ANY_HEADING = re.compile(r"^#{1,6}\s+")
_MALFORMED_TAG_HEADING = re.compile(r"^(?:###\s+|#{4,6}\s+.*[`/])")
_TAG_NAME = re.compile(r"^(?P<namespace>[a-z][a-z0-9-]*)/(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$")


def parse_tag_registry(path: Path) -> TagRegistry:
    """Parse a controlled-tag registry Markdown file without modifying it."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValidationError(
            f"Cannot read tag registry {path}: {error}. Check that the configured file exists."
        ) from error

    entries: list[RegistryEntry] = []
    names: set[str] = set()
    index = 0
    while index < len(lines):
        match = _TAG_HEADING.match(lines[index])
        if match is None:
            if _MALFORMED_TAG_HEADING.match(lines[index]):
                raise ValidationError(
                    f"Malformed tag heading in tag registry {path}: {lines[index]!r}. "
                    "Use exactly: ### `namespace/tag-name`."
                )
            index += 1
            continue

        name = match.group(1)
        _validate_name(name, path)
        if name in names:
            raise ValidationError(
                f"Duplicate tag `{name}` in tag registry {path}. Remove or rename one definition."
            )

        index += 1
        definition_lines: list[str] = []
        while index < len(lines) and _ANY_HEADING.match(lines[index]) is None:
            definition_lines.append(lines[index])
            index += 1
        definition = "\n".join(definition_lines).strip()
        if not definition:
            raise ValidationError(
                f"Tag `{name}` in tag registry {path} has no definition. "
                "Add explanatory text below it."
            )
        names.add(name)
        entries.append(RegistryEntry(name=name, definition=definition))

    return TagRegistry(entries=tuple(entries))


def _validate_name(name: str, path: Path) -> None:
    match = _TAG_NAME.fullmatch(name)
    if match is None:
        raise ValidationError(
            f"Malformed tag `{name}` in tag registry {path}. Use lowercase kebab-case as "
            "`namespace/tag-name`."
        )

    namespace = match.group("namespace")
    if namespace not in TAG_NAMESPACES:
        allowed = ", ".join(TAG_NAMESPACES)
        raise ValidationError(
            f"Unknown tag namespace `{namespace}` in tag registry {path}. "
            f"Use one of: {allowed}."
        )

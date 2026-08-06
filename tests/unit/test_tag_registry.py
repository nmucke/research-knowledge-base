"""Tests for strict controlled-tag registry parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from research_kb.exceptions import ValidationError
from research_kb.tag_registry import TAG_NAMESPACES, RegistryEntry, parse_tag_registry


def _write_registry(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tag-registry.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_tag_registry_preserves_entries_and_definitions(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """# Tag registry

## Domain

### `domain/fluid-dynamics`

Fluid flow and turbulence.

More detail.

## Method

### `method/diffusion-models`

Diffusion-based generative models.
""",
    )

    registry = parse_tag_registry(path)

    assert registry.entries == (
        RegistryEntry(
            name="domain/fluid-dynamics",
            definition="Fluid flow and turbulence.\n\nMore detail.",
        ),
        RegistryEntry(
            name="method/diffusion-models",
            definition="Diffusion-based generative models.",
        ),
    )
    assert registry.names == ("domain/fluid-dynamics", "method/diffusion-models")
    assert registry.namespaces == ("domain", "method")
    assert TAG_NAMESPACES == ("domain", "method", "task", "property", "model", "data")


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("domain/Fluid-Dynamics", "Malformed tag"),
        ("domain", "Malformed tag"),
        ("unknown/topic", "Unknown tag namespace"),
    ],
)
def test_parse_tag_registry_rejects_invalid_tag_names(
    tmp_path: Path, tag: str, message: str
) -> None:
    path = _write_registry(tmp_path, f"### `{tag}`\n\nDefinition.\n")

    with pytest.raises(ValidationError, match=message) as error:
        parse_tag_registry(path)

    assert str(path) in str(error.value)


def test_parse_tag_registry_rejects_duplicate_or_blank_definitions(tmp_path: Path) -> None:
    duplicate = _write_registry(
        tmp_path,
        "### `domain/weather`\n\nWeather.\n\n### `domain/weather`\n\nForecasting.\n",
    )

    with pytest.raises(ValidationError, match="Duplicate tag"):
        parse_tag_registry(duplicate)

    blank = _write_registry(tmp_path, "### `domain/weather`\n\n## Next\n")

    with pytest.raises(ValidationError, match="has no definition"):
        parse_tag_registry(blank)


def test_registry_entries_are_immutable(tmp_path: Path) -> None:
    registry = parse_tag_registry(_write_registry(tmp_path, "### `data/benchmark`\n\nA dataset.\n"))

    with pytest.raises(PydanticValidationError):
        registry.entries[0].name = "data/other"  # type: ignore[misc]


@pytest.mark.parametrize(
    "heading",
    ["### domain/Bad Tag", "### `domain/weather", "#### `domain/weather`"],
)
def test_parse_tag_registry_rejects_malformed_tag_headings(
    tmp_path: Path, heading: str
) -> None:
    path = _write_registry(tmp_path, f"# Tag registry\n\n{heading}\n\nDefinition.\n")

    with pytest.raises(ValidationError, match="Malformed tag heading"):
        parse_tag_registry(path)


def test_parse_tag_registry_reports_non_utf8_input(tmp_path: Path) -> None:
    path = tmp_path / "tag-registry.md"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(ValidationError, match="Cannot read tag registry"):
        parse_tag_registry(path)

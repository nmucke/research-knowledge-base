"""Inspect built distributions without importing from the source checkout."""

from __future__ import annotations

import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PRIVATE_NAMES = {
    ".env",
    ".git",
    ".research",
    "references.bib",
    "REPOSITORY_ASSESSMENT.md",
    "SKILL_RECOMMENDATIONS.md",
    "IMPLEMENTATION.md",
}
RESEARCH_SKILLS = {
    "curation-approval",
    "literature-discovery",
    "paper-review",
    "project-curation",
    "research-synthesis",
    "workspace-initialization",
}


def _logical_parts(name: str, *, sdist: bool) -> tuple[str, ...]:
    parts = PurePosixPath(name).parts
    return parts[1:] if sdist and parts else parts


def _check_names(names: list[str], *, sdist: bool) -> None:
    logical = [_logical_parts(name, sdist=sdist) for name in names]
    violations = [
        "/".join(parts)
        for parts in logical
        if parts
        and (
            parts[0] == "vault"
            or any(part in PRIVATE_NAMES for part in parts)
            or parts[-1].endswith(("ASSESSMENT.md", "RECOMMENDATIONS.md"))
        )
    ]
    if violations:
        raise AssertionError(f"private paths in distribution: {violations}")


def inspect_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        _check_names(names, sdist=False)
        symlinks = [
            item.filename
            for item in infos
            if stat.S_ISLNK((item.external_attr >> 16) & 0xFFFF)
        ]
        if symlinks:
            raise AssertionError(f"symlinks in wheel: {symlinks}")
        for skill in RESEARCH_SKILLS:
            required = f"research_kb/assets/skills/{skill}/SKILL.md"
            if required not in names:
                raise AssertionError(f"wheel is missing {required}")
        for required in (
            "research_kb/assets/agents/AGENTS.md",
            "research_kb/assets/vault/System/Templates/Paper.md",
        ):
            if required not in names:
                raise AssertionError(f"wheel is missing {required}")


def inspect_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [item.name for item in members]
        _check_names(names, sdist=True)
        unsafe_links = []
        for item in members:
            if not (item.issym() or item.islnk()):
                continue
            target = PurePosixPath(item.linkname)
            if target.is_absolute() or ".." in target.parts:
                unsafe_links.append(f"{item.name} -> {item.linkname}")
        if unsafe_links:
            raise AssertionError(f"unsafe links in sdist: {unsafe_links}")
        logical = {"/".join(_logical_parts(name, sdist=True)) for name in names}
        for required in (
            "AGENTS.md",
            "CLAUDE.md",
            ".agents/skills/workflow-evaluation/SKILL.md",
            ".github/workflows/test.yml",
            "src/research_kb/assets/skills/paper-review/SKILL.md",
        ):
            if required not in logical:
                raise AssertionError(f"sdist is missing {required}")


def main(arguments: list[str]) -> None:
    if len(arguments) != 2:
        raise SystemExit("usage: inspect_distribution.py DIST.whl DIST.tar.gz")
    paths = [Path(argument) for argument in arguments]
    wheel = next((path for path in paths if path.suffix == ".whl"), None)
    sdist = next((path for path in paths if path.name.endswith(".tar.gz")), None)
    if wheel is None or sdist is None:
        raise SystemExit("provide one wheel and one .tar.gz sdist")
    inspect_wheel(wheel)
    inspect_sdist(sdist)


if __name__ == "__main__":
    main(sys.argv[1:])

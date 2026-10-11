"""Check every skill folder against the Agent Skills format and the rules of this repo.

Checks, per skills/<name>/SKILL.md (format read on agentskills.io, 2026-10-11):
- frontmatter present, with name, description and license;
- name: 1 to 64 characters, lowercase letters, digits and hyphens, equal to the folder name;
- description: 1 to 1024 characters;
- file at most 500 lines, body at most 5000 tokens (estimated at 4 characters per token);
- the sentence that refuses personalised advice is present;
- a scripts/ folder with at least one Python script and a tests/ folder exist.

Repo-wide: no em dash (U+2014) and no en dash (U+2013) in any text file written by hand.
Recorded API responses and fixtures are data copied verbatim and are not checked.

Exit code 1 and one line per problem when something fails, 0 and a short summary otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_LINES = 500
MAX_BODY_TOKENS = 5000
MAX_DESCRIPTION = 1024
DISCLAIMER = "personalised advice"
TEXT_SUFFIXES = {".md", ".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg"}
DASH_EXEMPT_PARTS = {"recorded", "fixtures", ".git", ".venv", ".pytest_cache", ".ruff_cache"}
BAD_DASHES = {"—": "em dash U+2014", "–": "en dash U+2013"}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, text[end + 5 :]


def check_skill(folder: Path) -> list[str]:
    problems: list[str] = []
    skill_md = folder / "SKILL.md"
    rel = skill_md.relative_to(ROOT).as_posix()
    if not skill_md.is_file():
        return [f"{folder.name}: SKILL.md missing"]
    text = skill_md.read_text(encoding="utf-8")
    parsed = parse_frontmatter(text)
    if parsed is None:
        return [f"{rel}: no YAML frontmatter"]
    meta, body = parsed
    name = meta.get("name", "")
    if not NAME_RE.match(name) or not 1 <= len(name) <= 64:
        problems.append(f"{rel}: name {name!r} is not 1 to 64 lowercase letters, digits, hyphens")
    if name != folder.name:
        problems.append(f"{rel}: name {name!r} differs from folder {folder.name!r}")
    description = meta.get("description", "")
    if not 1 <= len(description) <= MAX_DESCRIPTION:
        problems.append(f"{rel}: description has {len(description)} characters (1 to 1024)")
    if not meta.get("license"):
        problems.append(f"{rel}: license missing from frontmatter")
    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    if lines > MAX_LINES:
        problems.append(f"{rel}: {lines} lines, more than {MAX_LINES}")
    tokens = len(body) // 4
    if tokens > MAX_BODY_TOKENS:
        problems.append(f"{rel}: body about {tokens} tokens, more than {MAX_BODY_TOKENS}")
    if DISCLAIMER not in text:
        problems.append(f"{rel}: the sentence refusing personalised advice is missing")
    if not list((folder / "scripts").glob("*.py")):
        problems.append(f"{folder.name}: no Python script in scripts/")
    if not list((folder / "tests").glob("test_*.py")):
        problems.append(f"{folder.name}: no test in tests/")
    return problems


def check_dashes() -> list[str]:
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if DASH_EXEMPT_PARTS & set(path.relative_to(ROOT).parts):
            continue
        if path.name == Path(__file__).name:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path.relative_to(ROOT).as_posix()}: not UTF-8")
            continue
        for ch, label in BAD_DASHES.items():
            if ch in text:
                line = text[: text.index(ch)].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT).as_posix()}:{line}: {label}")
    return problems


def main() -> int:
    folders = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    problems: list[str] = []
    for folder in folders:
        problems += check_skill(folder)
    problems += check_dashes()
    for p in problems:
        print(p)
    if problems:
        print(f"{len(problems)} problem(s)")
        return 1
    print(f"{len(folders)} skill(s) checked, no problem, no em dash or en dash in text files")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Quick validation script for Claude-compatible skills."""

from __future__ import annotations

from pathlib import Path
import re
import sys

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

SKILLS_BASE_PATH = Path("/home/ubuntu/skills")


def resolve_skill_path(skill_path_or_name: str) -> Path:
    path = Path(skill_path_or_name)
    if path.is_absolute():
        return path
    return SKILLS_BASE_PATH / skill_path_or_name


def _parse_frontmatter(text: str) -> tuple[bool, str, dict]:
    if not text.startswith("---"):
        return False, "No YAML frontmatter found", {}
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format", {}
    frontmatter_text = match.group(1)
    if yaml is not None:
        try:
            frontmatter = yaml.safe_load(frontmatter_text)
        except Exception as exc:
            return False, f"Invalid YAML in frontmatter: {exc}", {}
    else:
        frontmatter = {}
        for line in frontmatter_text.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip().strip("\"'")
    if not isinstance(frontmatter, dict):
        return False, "Frontmatter must be a YAML dictionary", {}
    return True, "ok", frontmatter


def validate_skill(skill_path_or_name: str) -> tuple[bool, str]:
    skill_path = resolve_skill_path(skill_path_or_name)
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text(encoding="utf-8")
    ok, message, frontmatter = _parse_frontmatter(content)
    if not ok:
        return False, message

    allowed = {"name", "description", "license", "allowed-tools", "metadata"}
    unexpected = set(frontmatter.keys()) - allowed
    if unexpected:
        return False, (
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected))}. "
            f"Allowed properties are: {', '.join(sorted(allowed))}"
        )

    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not name.strip():
        return False, "Missing or invalid 'name' in frontmatter"
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case"
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens"
    if len(name) > 64:
        return False, "Name is too long. Maximum is 64 characters."
    if not isinstance(description, str) or not description.strip():
        return False, "Missing or invalid 'description' in frontmatter"
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)"
    if len(description) > 1024:
        return False, "Description is too long. Maximum is 1024 characters."
    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: quick_validate.py <skill-name-or-path>")
        sys.exit(1)
    valid, msg = validate_skill(sys.argv[1])
    print(msg)
    sys.exit(0 if valid else 1)

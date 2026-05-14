#!/usr/bin/env python3
"""Skill Initializer - creates a new skill from template."""

from __future__ import annotations

from pathlib import Path
import re
import sys

SKILLS_BASE_PATH = Path("/home/ubuntu/skills")

SKILL_TEMPLATE = """---
name: {skill_name}
description: [TODO: Complete and informative explanation of what this skill does and when to use it.]
---

# {skill_title}

## Overview

[TODO: 1-2 sentences explaining what this skill enables.]

## Workflow

[TODO: Add concrete reusable steps. Keep this concise and move long details to references/.]

## Resources

- Use scripts/ for deterministic helper code.
- Use references/ for documentation loaded only when needed.
- Use templates/ for output assets or boilerplate.
"""

EXAMPLE_SCRIPT = '''#!/usr/bin/env python3
"""Example helper script for {skill_name}."""


def main():
    print("This is an example script for {skill_name}")


if __name__ == "__main__":
    main()
'''

EXAMPLE_REFERENCE = """# Reference Documentation for {skill_title}

Replace this placeholder with detailed reference content or delete it if not needed.
"""

EXAMPLE_TEMPLATE = """# Example Template File

Replace this placeholder with actual template files or delete it if not needed.
"""


def title_case_skill_name(skill_name: str) -> str:
    return " ".join(word.capitalize() for word in skill_name.split("-"))


def validate_name(skill_name: str) -> bool:
    return bool(re.match(r"^[a-z0-9][a-z0-9-]{0,63}$", skill_name)) and "--" not in skill_name


def init_skill(skill_name: str) -> Path | None:
    if not validate_name(skill_name):
        print("Error: skill name must be lower-case hyphen-case, max 64 chars")
        return None
    skill_dir = SKILLS_BASE_PATH / skill_name
    if skill_dir.exists():
        print(f"Error: skill directory already exists: {skill_dir}")
        return None
    skill_title = title_case_skill_name(skill_name)
    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
        (skill_dir / "SKILL.md").write_text(
            SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title),
            encoding="utf-8",
        )
        scripts_dir = skill_dir / "scripts"
        references_dir = skill_dir / "references"
        templates_dir = skill_dir / "templates"
        scripts_dir.mkdir()
        references_dir.mkdir()
        templates_dir.mkdir()
        script = scripts_dir / "example.py"
        script.write_text(EXAMPLE_SCRIPT.format(skill_name=skill_name), encoding="utf-8")
        script.chmod(0o755)
        (references_dir / "api_reference.md").write_text(
            EXAMPLE_REFERENCE.format(skill_title=skill_title),
            encoding="utf-8",
        )
        (templates_dir / "example_template.txt").write_text(EXAMPLE_TEMPLATE, encoding="utf-8")
    except Exception as exc:
        print(f"Error creating skill: {exc}")
        return None
    print(f"Skill '{skill_name}' initialized at {skill_dir}")
    return skill_dir


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: init_skill.py <skill-name>")
        sys.exit(1)
    sys.exit(0 if init_skill(sys.argv[1]) else 1)

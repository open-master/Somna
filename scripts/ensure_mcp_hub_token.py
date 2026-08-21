#!/usr/bin/env python3
"""Fill an empty MCP_HUB_INTERNAL_TOKEN in .env without overwriting a set value."""

from __future__ import annotations

import re
import secrets
from pathlib import Path


def main() -> None:
    path = Path(".env")
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^MCP_HUB_INTERNAL_TOKEN=(.*)$", text, re.M)
    if match and match.group(1).strip():
        return
    token = secrets.token_urlsafe(32)
    if match:
        text = re.sub(
            r"^MCP_HUB_INTERNAL_TOKEN=.*$",
            f"MCP_HUB_INTERNAL_TOKEN={token}",
            text,
            count=1,
            flags=re.M,
        )
    else:
        text = text.rstrip() + f"\nMCP_HUB_INTERNAL_TOKEN={token}\n"
    path.write_text(text, encoding="utf-8")
    print("✓ generated MCP_HUB_INTERNAL_TOKEN")


if __name__ == "__main__":
    main()

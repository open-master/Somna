"""Pytest fixtures — isolate sandbox root per test session."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

# Configure the sandbox root BEFORE importing app modules.
_TMP = tempfile.TemporaryDirectory(prefix="somna_mcp_hub_tests_")
os.environ["SANDBOX_ROOT"] = _TMP.name

from app.config import get_settings
from app.sandbox import manager as sbx_mod


@pytest.fixture(autouse=True)
def _fresh_sandbox_manager() -> Iterator[None]:
    # Reset the singleton so each test starts with a clean in-memory state.
    sbx_mod.reset_for_tests(None)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield

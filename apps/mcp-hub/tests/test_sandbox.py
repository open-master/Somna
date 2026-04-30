from __future__ import annotations

import pytest

from app.config import get_settings
from app.sandbox.manager import PathEscapeError, get_sandbox_manager


def test_create_and_get():
    sm = get_sandbox_manager(get_settings())
    rec = sm.create(session_id="alpha")
    assert rec.id == "alpha"
    got = sm.get("alpha")
    assert got is not None
    assert got.workdir.endswith("alpha")


def test_safe_join_rejects_escape():
    sm = get_sandbox_manager(get_settings())
    sm.create(session_id="beta")
    # absolute is normalized to sandbox-relative
    p = sm.safe_join("beta", "/sub/file.txt")
    assert str(p).endswith("beta/sub/file.txt")

    with pytest.raises(PathEscapeError):
        sm.safe_join("beta", "../../../etc/passwd")


def test_safe_join_root():
    sm = get_sandbox_manager(get_settings())
    sm.create(session_id="gamma")
    p = sm.safe_join("gamma", ".")
    assert p.exists()


def test_delete():
    sm = get_sandbox_manager(get_settings())
    sm.create(session_id="toremove")
    assert sm.delete("toremove") is True
    assert sm.delete("toremove") is False

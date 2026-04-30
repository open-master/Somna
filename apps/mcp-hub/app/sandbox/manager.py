"""Sandbox manager.

M2 strategy: directory-per-sandbox under `SANDBOX_ROOT`.
- Each sandbox has a unique id; workdir is `<root>/<sandbox_id>`.
- Paths accessed by tools are resolved and enforced to stay within the workdir
  (see `SandboxManager.safe_join`).
- Metadata lives in-memory for now; rebuilt from disk on boot.

M3 upgrade path: replace the bare directory with a per-session container
(Docker-in-Docker with `SANDBOX_IMAGE`). Interface stays the same.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock

from app.config import Settings


class SandboxError(Exception):
    pass


class PathEscapeError(SandboxError):
    """Raised when a tool tries to access a path outside its sandbox."""


@dataclass
class SandboxRecord:
    id: str
    session_id: str | None
    workdir: str
    created_at: float
    last_used: float
    meta: dict[str, str] = field(default_factory=dict)


class SandboxManager:
    """In-process registry of sandboxes. Thread-safe."""

    META_FILE = ".somna_sandbox.json"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root = Path(settings.sandbox_root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._records: dict[str, SandboxRecord] = {}
        self._rehydrate()

    # ---------- lifecycle ----------

    def create(self, session_id: str | None = None) -> SandboxRecord:
        sandbox_id = session_id or uuid.uuid4().hex[:16]
        with self._lock:
            existing = self._records.get(sandbox_id)
            if existing:
                existing.last_used = time.time()
                return existing
            workdir = self._root / sandbox_id
            workdir.mkdir(parents=True, exist_ok=True)
            rec = SandboxRecord(
                id=sandbox_id,
                session_id=session_id,
                workdir=str(workdir),
                created_at=time.time(),
                last_used=time.time(),
            )
            self._records[sandbox_id] = rec
            self._persist(rec)
            return rec

    def get(self, sandbox_id: str) -> SandboxRecord | None:
        with self._lock:
            rec = self._records.get(sandbox_id)
            if rec is None and (self._root / sandbox_id).is_dir():
                rec = SandboxRecord(
                    id=sandbox_id,
                    session_id=None,
                    workdir=str(self._root / sandbox_id),
                    created_at=time.time(),
                    last_used=time.time(),
                )
                self._records[sandbox_id] = rec
            return rec

    def get_or_create(self, sandbox_id: str) -> SandboxRecord:
        rec = self.get(sandbox_id)
        if rec:
            rec.last_used = time.time()
            return rec
        return self.create(session_id=sandbox_id)

    def delete(self, sandbox_id: str) -> bool:
        with self._lock:
            rec = self._records.pop(sandbox_id, None)
            workdir = self._root / sandbox_id
            if workdir.exists():
                shutil.rmtree(workdir, ignore_errors=True)
            return rec is not None

    def list_all(self) -> list[SandboxRecord]:
        with self._lock:
            return list(self._records.values())

    # ---------- path safety ----------

    def safe_join(self, sandbox_id: str, relative_path: str) -> Path:
        """Resolve `relative_path` inside the sandbox and fail on escape.

        Accepts absolute paths (they are treated as sandbox-rooted), relative
        paths, and segments like `../`. The resolved path MUST be within the
        sandbox workdir.
        """
        rec = self.get_or_create(sandbox_id)
        root = Path(rec.workdir).resolve()

        p = relative_path or ""
        # Normalize: strip leading slash so we never leave the sandbox.
        p = p.lstrip("/\\")
        candidate = (root / p).resolve()

        if root != candidate and root not in candidate.parents:
            raise PathEscapeError(
                f"path {relative_path!r} escapes sandbox {sandbox_id!r}"
            )
        return candidate

    # ---------- internals ----------

    def _persist(self, rec: SandboxRecord) -> None:
        meta_path = Path(rec.workdir) / self.META_FILE
        try:
            meta_path.write_text(json.dumps(asdict(rec), ensure_ascii=False, indent=2))
        except OSError:
            # non-fatal; the record exists in-memory
            pass

    def _rehydrate(self) -> None:
        if not self._root.is_dir():
            return
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            meta_path = entry / self.META_FILE
            if meta_path.is_file():
                try:
                    data = json.loads(meta_path.read_text())
                    rec = SandboxRecord(**data)
                    self._records[rec.id] = rec
                    continue
                except (OSError, ValueError, TypeError):
                    pass
            # Fallback: synthesize from dir name
            self._records[entry.name] = SandboxRecord(
                id=entry.name,
                session_id=None,
                workdir=str(entry),
                created_at=os.path.getctime(entry),
                last_used=os.path.getmtime(entry),
            )


_singleton: SandboxManager | None = None


def get_sandbox_manager(settings: Settings) -> SandboxManager:
    global _singleton
    if _singleton is None:
        _singleton = SandboxManager(settings)
    return _singleton


def reset_for_tests(settings: Settings | None = None) -> SandboxManager | None:
    global _singleton
    _singleton = SandboxManager(settings) if settings else None
    return _singleton

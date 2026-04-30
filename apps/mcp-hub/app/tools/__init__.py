"""Tool plugins registered into the global registry on import."""

from __future__ import annotations

# Side-effect imports to populate the registry.
from . import filesystem as _fs  # noqa: F401
from . import search as _search  # noqa: F401
from . import shell as _shell  # noqa: F401

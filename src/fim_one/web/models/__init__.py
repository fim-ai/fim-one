"""Back-compat shim — ORM models moved to :mod:`fim_one.db.models` (Track B, 2026-05).

Importing ``fim_one.web.models`` used to drag in the entire FastAPI app via
``fim_one/web/__init__.py``. Models now live under :mod:`fim_one.db.models` so the
database/migration layers can populate ``Base.metadata`` without importing the web
layer. This shim keeps any lingering ``from fim_one.web.models import X`` working.

Prefer ``from fim_one.db.models import X`` in new code.
"""

from __future__ import annotations

from fim_one.db.models import *  # noqa: F401,F403
from fim_one.db.models import __all__ as __all__  # re-export the public surface

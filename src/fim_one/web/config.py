"""Validated configuration for the web layer.

Most of the FIM One web layer reads configuration directly from
``os.environ`` (see ``deps.py``). This module hosts a typed
:class:`Settings` object for subsystems whose config values need
cross-field validation — currently only the Stripe billing layer.

The intent is **not** to migrate every existing env var here; it is to
give billing a place where misconfiguration fails loudly at first use
(via :data:`settings`) instead of producing mysterious 500s later.
Validation runs lazily — importing this module never raises — so a
half-configured Stripe deploy does not break unrelated subsystems.

Pattern:

>>> from fim_one.web.config import settings
>>> if settings.STRIPE_SECRET_KEY:
...     stripe.api_key = settings.STRIPE_SECRET_KEY.get_secret_value()
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

#: Stripe secret/restricted key prefixes. Anything else (e.g. ``pk_live_``
#: which is the publishable key) is a programming error and should fail
#: at startup.
_STRIPE_SECRET_KEY_PREFIXES: tuple[str, ...] = (
    "sk_test_",
    "sk_live_",
    "rk_test_",
    "rk_live_",
)


def _read_secret(name: str) -> SecretStr | None:
    """Return ``SecretStr`` for ``$name`` if set and non-empty, else ``None``.

    Empty string / whitespace-only env values count as unset, which lets
    operators "delete" a key by blanking the entry without removing the
    line from their ``.env`` file.
    """
    raw = os.environ.get(name, "").strip()
    return SecretStr(raw) if raw else None


def _read_str(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw or default


class Settings(BaseModel):
    """Typed, validated subset of the runtime config.

    Constructed from ``os.environ`` at import time.  Any cross-field
    invariant is enforced via ``@model_validator(mode="after")`` so the
    process refuses to start with an inconsistent Stripe configuration.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    # ── Stripe billing (v1 MVP) ───────────────────────────────────────
    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_WEBHOOK_SECRET: SecretStr | None = None
    STRIPE_BILLING_RETURN_URL: str = "http://localhost:3000/settings?tab=billing"

    @model_validator(mode="after")
    def _validate_stripe(self) -> Settings:
        secret = self.STRIPE_SECRET_KEY
        webhook = self.STRIPE_WEBHOOK_SECRET

        # Partial config is the most likely outage cause: an operator
        # rotates one of the two values and forgets the other. Refuse
        # to boot rather than serve checkouts that webhooks can't ack.
        if (secret is None) != (webhook is None):
            raise ValueError(
                "Partial Stripe config: STRIPE_SECRET_KEY and "
                "STRIPE_WEBHOOK_SECRET must both be set or both unset. "
                "Got: STRIPE_SECRET_KEY="
                + ("set" if secret else "unset")
                + ", STRIPE_WEBHOOK_SECRET="
                + ("set" if webhook else "unset")
            )

        if secret is not None:
            value = secret.get_secret_value()
            if not value.startswith(_STRIPE_SECRET_KEY_PREFIXES):
                raise ValueError(
                    "Invalid Stripe secret key prefix: STRIPE_SECRET_KEY "
                    f"must start with one of {_STRIPE_SECRET_KEY_PREFIXES}. "
                    "(Hint: did you paste the publishable key 'pk_*' by mistake?)"
                )

        return self

    @classmethod
    def from_env(cls) -> Settings:
        """Build a fresh ``Settings`` from the current ``os.environ`` snapshot.

        Exposed for tests that monkeypatch env vars and want to reread
        the config without restarting the process.
        """
        kwargs: dict[str, Any] = {
            "STRIPE_SECRET_KEY": _read_secret("STRIPE_SECRET_KEY"),
            "STRIPE_WEBHOOK_SECRET": _read_secret("STRIPE_WEBHOOK_SECRET"),
            "STRIPE_BILLING_RETURN_URL": _read_str(
                "STRIPE_BILLING_RETURN_URL",
                "http://localhost:3000/settings?tab=billing",
            ),
        }
        return cls(**kwargs)


class _LazySettings:
    """Lazy proxy that builds a :class:`Settings` on first attribute access.

    Why lazy: third-party libs (notably ``litellm``) call ``load_dotenv()``
    at import time, so ``.env`` lands in ``os.environ`` only **after**
    other top-level imports run. Validating Stripe config eagerly at
    module-import time would therefore see a half-loaded environment and
    surface confusing errors to anyone importing
    ``fim_one.db.models.user`` or similar unrelated paths. Constructing
    on first access defers validation to the first real use.
    """

    _instance: Settings | None = None

    def _resolve(self) -> Settings:
        if self._instance is None:
            self._instance = Settings.from_env()
        return self._instance

    def reset(self) -> None:
        """Drop the cached instance — used by tests after env mutation."""
        self._instance = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


#: Module-level lazy singleton. Validates on first attribute access.
#: Tests that mutate env should call ``settings.reset()`` to force
#: re-evaluation on the next access.
settings = _LazySettings()

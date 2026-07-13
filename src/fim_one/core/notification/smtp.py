"""Shared SMTP transport-mode resolution.

Both the system-email path (``fim_one.web.email``) and the notification
provider (``fim_one.core.notification.email_provider``) authenticate to SMTP
with ``server.login()``, so both must agree on when the connection is
encrypted. Keeping the rule in one place means a typo in ``SMTP_SSL`` cannot
downgrade one of them to cleartext while the other stays secure.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

SMTP_SSL_MODES = ("ssl", "tls", "none")


def resolve_ssl_mode() -> str:
    """Resolve ``SMTP_SSL`` to a known transport mode.

    Fails closed: an unrecognized value raises rather than silently falling
    back to the unencrypted path, which would put SMTP credentials on the wire
    in cleartext. Empty string remains a supported spelling of ``none``.
    """
    raw = os.getenv("SMTP_SSL", "ssl").strip().lower()
    mode = "none" if raw == "" else raw
    if mode not in SMTP_SSL_MODES:
        raise ValueError(
            f"SMTP_SSL must be one of {'/'.join(SMTP_SSL_MODES)} "
            f'(or "" for none), got {raw!r}'
        )
    return mode


def warn_plaintext(host: str, port: int) -> None:
    """Log that credentials are about to cross an unencrypted connection."""
    logger.warning(
        "SMTP_SSL=none: connecting to %s:%s without encryption. "
        "SMTP credentials are sent in cleartext. Set SMTP_SSL=ssl or tls "
        "for any host that is not a trusted local relay.",
        host,
        port,
    )

"""Registers what the assembly layer needs from the web layer.

``core.assembly`` does not import ``fim_one.web``; data only the web layer
has reaches it through ports. Every process that serves requests or runs
workflows registers them once, before the first turn.
"""

from __future__ import annotations

__all__ = ["register_assembly_ports"]


def register_assembly_ports() -> None:
    """Register the web layer's implementations of the assembly ports. Idempotent."""
    from fim_one.core.assembly.quota import set_quota_backend
    from fim_one.web.services.quota_backend import WebQuotaBackend

    set_quota_backend(WebQuotaBackend())

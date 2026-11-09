"""Shared visibility filter — subscription-based model.

After the marketplace redesign, org auto-visibility is removed.  Resources
are visible to a user only if:

1. The user owns the resource (any visibility setting), OR
2. The user has an explicit ResourceSubscription for it **and the resource
   is still shared** — ``visibility == "org"`` and not awaiting or refused
   by review.

Condition 2's second half is what makes unpublishing effective: a
subscription row alone used to grant access forever, so an owner who
reverted a resource to personal visibility could not actually take it back
short of deleting it.  The share state is re-read on every query, so
revocation applies to running conversations too.

Org-scoped resources are surfaced via subscriptions that are created when
org members subscribe from the Market page (or via auto-subscribe on
resource creation / org membership join).

The rule itself lives in :mod:`fim_one.core.assembly.visibility`, where chat
and workflow nodes apply it at use time.  This module re-exports the two
helpers the list pages under ``web/api/`` filter with.
"""
from __future__ import annotations

from fim_one.core.assembly.visibility import (
    build_visibility_filter,
    resolve_visibility,
)

__all__ = ["build_visibility_filter", "resolve_visibility"]

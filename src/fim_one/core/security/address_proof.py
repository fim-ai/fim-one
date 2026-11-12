"""Whether an email address has to be proven before it names an account.

With email verification switched on (the ``email_verification_enabled``
system setting, and SMTP configured so that codes can be sent), an address
names only an account that has proven it.  Lookups by address that grant
something to the account found (adding it to an organization, assigning it
an approval) refuse an account whose address is unproven.
"""

from __future__ import annotations

import os
from typing import Any

SETTING_EMAIL_VERIFICATION_ENABLED = "email_verification_enabled"


def smtp_configured() -> bool:
    """Whether the SMTP settings needed to send mail are present."""
    return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASS"))


async def address_proof_required(db: Any) -> bool:
    """Whether email verification is in force on this server."""
    from sqlalchemy import select

    from fim_one.db.models import SystemSetting

    raw = (
        await db.execute(
            select(SystemSetting.value).where(
                SystemSetting.key == SETTING_EMAIL_VERIFICATION_ENABLED
            )
        )
    ).scalar_one_or_none()
    return (raw or "").strip().lower() == "true" and smtp_configured()

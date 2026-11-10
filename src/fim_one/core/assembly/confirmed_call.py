"""A tool call that waits for a person's confirmation, kept as it will run.

Used by the workflow Connector step (``core/workflow/nodes.py``): the
arguments of the call are stored on the confirmation request encrypted,
with what the approver is shown of them (:func:`seal_call`,
:func:`open_call`), beside a fingerprint of what the call is and as whom
it is made (:func:`call_fingerprint`, :func:`credential_source`). What was
approved is what runs, or nothing does.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["call_fingerprint", "credential_source", "masked_env", "open_call", "seal_call"]


async def credential_source(db: Any, connector_id: str, user_id: str) -> str:
    """Whose credential a call by *user_id* through the connector carries:
    ``own`` if they saved one for it, else ``owner``."""
    from sqlalchemy import select

    from fim_one.db.models.connector_credential import ConnectorCredential

    own = (
        await db.execute(
            select(ConnectorCredential.id)
            .where(
                ConnectorCredential.connector_id == connector_id,
                ConnectorCredential.user_id == user_id,
            )
            .limit(1)
        )
    ).first()
    return "own" if own is not None else "owner"


#: An env value shorter than this is masked only where it is a whole argument
#: value. Replacing ``1`` or ``e`` wherever it occurs would turn ``1000`` into
#: ``{{env.RETRIES}}000`` and leave the approver unable to read the call.
_MASK_ANYWHERE_MIN_LENGTH = 6


def masked_env(value: Any, env_vars: dict[str, str]) -> Any:
    """*value* with the workflow's env var values replaced by ``{{env.NAME}}``.

    What an approver is shown: that a secret goes into the call, not the
    secret. The call itself is made with the real value.

    A value of six characters or more is replaced wherever it occurs in a
    text argument; a shorter one only where it is the whole argument. Text is
    read once, longest value first, so a placeholder is never rewritten by a
    later replacement. Numbers and booleans are shown as they are.
    """
    names = {
        text: name
        for name, text in sorted(env_vars.items(), reverse=True)
        if isinstance(text, str) and text
    }
    if not names:
        return value
    anywhere = sorted(
        (text for text in names if len(text) >= _MASK_ANYWHERE_MIN_LENGTH),
        key=len,
        reverse=True,
    )
    pattern = re.compile("|".join(re.escape(text) for text in anywhere)) if anywhere else None

    def placeholder(text: str) -> str:
        return "{{env." + names[text] + "}}"

    def mask(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: mask(item) for key, item in node.items()}
        if isinstance(node, list):
            return [mask(item) for item in node]
        if not isinstance(node, str):
            return node
        if node in names:
            return placeholder(node)
        if pattern is None:
            return node
        return pattern.sub(lambda match: placeholder(match.group(0)), node)

    return mask(value)


def seal_call(arguments: dict[str, Any], env_vars: dict[str, str]) -> str:
    """The arguments of a call waiting for confirmation, as stored on its request.

    Encrypted with the credential key: the exact arguments that will be
    sent (``args``) and what the approver is shown (``shown``, the same
    with env var values masked). Nothing of them is kept in the clear.
    """
    from fim_one.core.security.encryption import encrypt_credential

    plain = json.loads(json.dumps(arguments, default=str, ensure_ascii=False))
    return encrypt_credential({"args": plain, "shown": masked_env(plain, env_vars)})


def open_call(payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(arguments to send, arguments to show)`` of a confirmation request's payload.

    ``(None, None)`` when the payload holds no sealed call or it cannot be
    decrypted with this instance's key.
    """
    from fim_one.core.security.encryption import decrypt_credential

    sealed = payload.get("sealed") if isinstance(payload, dict) else None
    if not isinstance(sealed, str):
        return None, None
    opened = decrypt_credential(sealed)
    args, shown = opened.get("args"), opened.get("shown")
    return (
        args if isinstance(args, dict) else None,
        shown if isinstance(shown, dict) else None,
    )


def call_fingerprint(connector: Any, action: Any, credential_source: str = "") -> str:
    """What a confirmation of this action was a confirmation of.

    A hash of where the call goes, as whom, and how it is built: the
    connector's address, authentication type and configuration (hashed,
    never kept), whether it lends its owner's credential, whose credential
    this run will use (*credential_source*: the runner's own, or the
    owner's), and the action's method, path, parameter schema, body
    template, response extraction and flag. If it differs between the
    request and the approval, the person approved something else.
    """
    import hashlib

    auth_config = json.dumps(connector.auth_config, sort_keys=True, default=str)
    definition = {
        "connector_id": connector.id,
        "base_url": connector.base_url,
        "auth_type": connector.auth_type,
        "auth_config": hashlib.sha256(auth_config.encode("utf-8")).hexdigest(),
        "allow_fallback": bool(connector.allow_fallback),
        "credential_source": credential_source,
        "action_id": action.id,
        "name": action.name,
        "method": action.method,
        "path": action.path,
        "parameters_schema": action.parameters_schema,
        "request_body_template": action.request_body_template,
        "response_extract": action.response_extract,
        "requires_confirmation": bool(action.requires_confirmation),
    }
    text = json.dumps(definition, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

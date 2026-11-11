"""No secret reaches an audit, sign-in or review row, the CSV export or the list.

One long session of the application: every kind of action the audit work
records, each carrying a secret with a recognisable marker (``CNRY<nn>``)
in the place a real one would be: a password, a token in a URL's path or
query, a key in a header, an env value, a webhook address, SQL. Then every
row of the three tables, the CSV export, the audit list and the sign-in
list are searched for every marker.

Two tables say what is expected. :data:`BY_DESIGN` are the markers that do
appear, each with where and why (a name the user chose, an email address,
a directory). Everything else must be absent: a marker that turns up
anywhere else fails the test, and so does a by-design marker that is no
longer there (the table would then be claiming something untrue).

The secrets have the shape real ones have where the rule that removes them
goes by shape: a token in a URL's path is long and mixed, as tokens are. A
short worded path segment is kept by that rule, on purpose (it cannot be
told from an API version or a tenant name); it is in :data:`BY_DESIGN`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fim_one.core.audit import refusals
from fim_one.db.models import User, UserOAuthBinding
from fim_one.db.models.connector import Connector
from fim_one.db.models.conversation import Conversation
from fim_one.db.models.workflow import Workflow
from fim_one.web.auth import create_oauth_state
from fim_one.web.ip_rules import parse_rules
from fim_one.web.oauth import OAuthProvider, OAuthUserInfo
from tests.audit.harness import CLIENT_IP, World, _row_text, world  # noqa: F401
from tests.totp import current_code

ANY = tuple(range(200, 600))
DYN: dict[str, str] = {}  # secrets the server generated: keys, codes, tokens

# Markers that appear in a row, where, and why that is intended.
BY_DESIGN: dict[str, str] = {
    "CNRY08": "api_key.create target label: the name the user gave the key",
    "CNRY21": "sensitive_word.create: the word is what the administrator acted on",
    "CNRY23": "user.update changes: the user's new email address (personal data, by the design)",
    "CNRY36": "channel.update changes: the NAME of a config key (its value is not recorded)",
    "CNRY43": "connector base_url: a short worded path segment, kept (not token-shaped)",
    "CNRY71": "mcp_server working_dir: a directory, recorded as where the server runs",
    "CNRY72": "mcp_server command: the program's path, recorded as what is run",
    "CNRY73": "mcp_server working_dir after the update: a directory",
    "CNRY84": "agent.update changes: the name of a tool category",
    "CNRY93": "security.ip_blocked: the caller's user agent, recorded with every request",
    "CNRY94": "login_history: an email-shaped value typed for an account that does not exist",
}


def _pad(marker: str, length: int = 32) -> str:
    """A token-shaped value (long, letters and digits mixed) carrying *marker*."""
    filler = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    return (marker + filler)[:length]


async def do(world: World, label: str, method: str, path: str, **kw: Any) -> Any:
    """One request of the session. Any status is accepted here: the steps
    that must succeed for the test to mean something are checked at the end,
    by the actions that were recorded."""
    del label
    return await world.call(method, path, expect=ANY, **kw)


def data(r: Any) -> Any:
    try:
        j = r.json()
        return j.get("data", j) if isinstance(j, dict) else j
    except Exception:  # noqa: BLE001
        return {}


async def _hits(world: World, root: User) -> dict[str, set[str]]:
    """marker -> the places it was found in."""
    rows: list[tuple[str, str]] = []
    for event in await world.events():
        rows.append((f"audit_logs[{event.action}]", _row_text(event)))
    for login in await world.logins():
        rows.append((f"login_history[{login.method}/{login.failure_reason}]", _row_text(login)))
    for review in await world.reviews():
        rows.append((f"review_log[{review.action}]", _row_text(review)))
    export = await world.call("GET", "/api/admin/audit-log/export", by=root)
    listing = await world.call("GET", "/api/admin/audit-log?size=200", by=root)
    logins = await world.call("GET", "/api/admin/login-history?size=200", by=root)
    rows += [
        ("CSV export", export.text),
        ("audit list JSON", listing.text),
        ("login-history JSON", logins.text),
    ]
    needles = {f"CNRY{n:02d}": f"CNRY{n:02d}" for n in range(100)}
    needles.update({name: value for name, value in DYN.items() if value})
    hits: dict[str, set[str]] = {}
    for where, text in rows:
        lowered = text.lower()
        for label, needle in needles.items():
            if needle.lower() in lowered:
                hits.setdefault(label, set()).add(where)
    return hits


@pytest.fixture()
def github() -> Iterator[dict[str, Any]]:
    identity: dict[str, Any] = {
        "info": OAuthUserInfo(provider="github", id="gh-1001", username="octo",
                              email="octo@example.test", display_name="Octo", email_verified=True)
    }
    provider = MagicMock(spec=OAuthProvider)
    provider.name = "github"

    async def _info(_p: Any, _t: str) -> OAuthUserInfo:
        return identity["info"]  # type: ignore[no-any-return]

    with (
        patch("fim_one.web.api.oauth.get_provider", return_value=provider),
        patch("fim_one.web.api.oauth.exchange_code", AsyncMock(return_value="CNRY90-provider-access-token")),
        patch("fim_one.web.api.oauth.fetch_user_info", _info),
    ):
        yield identity


@pytest.mark.timeout(300)
async def test_no_secret_reaches_a_row_the_export_or_the_list(world: World, github: dict[str, Any]) -> None:
    await world.enable_module("skills")
    await world.enable_module("workflows")
    root = await world.user("root", admin=True, password="RootPass-CNRY99-x1!")
    alice = await world.user("alice", password="CNRY01-alice-pass-A1!")
    bob = await world.user("bob", password="Bob-pass-A1!xx")

    # ---- A. sign-in and own account ------------------------------------
    await do(world, "A1 register", "POST", "/api/auth/register",
             json={"email": "newbie@example.test", "password": "CNRY02-regpass-A1!", "privacy_accepted": True})
    await do(world, "A2 login wrong pw", "POST", "/api/auth/login",
             json={"email": alice.email, "password": "CNRY03-wrongpass"})
    await do(world, "A3 login unknown (password typed as email)", "POST", "/api/auth/login",
             json={"email": "CNRY04-Passw0rd-typed-as-email!", "password": "x"})
    await do(world, "A3b login unknown, an address", "POST", "/api/auth/login",
             json={"email": "cnry94-ghost@example.test", "password": "x"})
    await do(world, "A4 login ok", "POST", "/api/auth/login",
             json={"email": alice.email, "password": "CNRY01-alice-pass-A1!"})
    await do(world, "A5 login-with-code", "POST", "/api/auth/login-with-code",
             json={"email": alice.email, "code": "CNRY05"})
    await do(world, "A6 change-password", "POST", "/api/auth/change-password", by=alice,
             json={"current_password": "CNRY01-alice-pass-A1!", "new_password": "CNRY06-newpass-A1!"})
    r = await do(world, "A7 2fa setup", "POST", "/api/auth/2fa/setup", by=alice)
    if r is not None and r.status_code == 200:
        secret = r.json().get("secret", "")
        DYN["totp_secret"] = secret
        r2 = await do(world, "A8 2fa enable", "POST", "/api/auth/2fa/enable", by=alice,
                      json={"code": current_code(secret)})
        if r2 is not None and r2.status_code == 200:
            for i, c in enumerate(r2.json().get("backup_codes", [])[:3]):
                DYN[f"backup_code_{i}"] = c
        r3 = await do(world, "A9 login with 2fa pending", "POST", "/api/auth/login",
                      json={"email": alice.email, "password": "CNRY06-newpass-A1!"})
        if r3 is not None and r3.status_code == 200 and r3.json().get("temp_token"):
            DYN["2fa_temp_token"] = r3.json()["temp_token"]
            await do(world, "A10 verify-2fa wrong", "POST", "/api/auth/login/verify-2fa",
                     json={"temp_token": r3.json()["temp_token"], "code": "CNRY07"})
        r4 = await do(world, "A11 backup codes regen", "POST", "/api/auth/2fa/backup-codes", by=alice,
                      json={"password": "CNRY06-newpass-A1!"})
        if r4 is not None and r4.status_code == 200:
            DYN["backup_code_regen"] = r4.json().get("backup_codes", [""])[0]
        await do(world, "A12 2fa disable", "POST", "/api/auth/2fa/disable", by=alice,
                 json={"password": "CNRY06-newpass-A1!"})
    r = await do(world, "A13 own api key", "POST", "/api/me/api-keys", by=alice, json={"name": "ci CNRY08name"})
    if r is not None and r.status_code == 201:
        DYN["own_api_key_full"] = r.json().get("key", "") or data(r).get("key", "")
        DYN["own_api_key_tail"] = DYN["own_api_key_full"][8:]
    # By someone else: it ends every session of the account it is done on.
    dave = await world.user("dave")
    await do(world, "A14 revoke sessions", "POST", "/api/me/sessions/revoke-all", by=dave)
    await do(world, "A15 profile username", "PATCH", "/api/auth/profile", by=bob, json={"username": "bob-renamed"})
    await do(world, "A16 change-email request", "POST", "/api/auth/change-email/request", by=bob,
             json={"new_email": "bob2@example.test", "password": "CNRY09-wrong"})
    await do(world, "A17 export own data", "GET", "/api/auth/me/export", by=bob)

    # ---- OAuth -----------------------------------------------------------
    octo = await world.user("octo")
    await world.add(UserOAuthBinding(user_id=octo.id, provider="github", oauth_id="gh-1001", email=octo.email))
    state = create_oauth_state("login", None)
    DYN["oauth_state"] = state
    r = await do(world, "O1 oauth callback", "GET", "/api/auth/oauth/github/callback",
                 params={"code": "CNRY10-oauth-code", "state": state})
    if r is not None and "location" in r.headers:
        loc = r.headers["location"]
        if "access_token=" in loc:
            DYN["oauth_issued_access"] = loc.split("access_token=")[1].split("&")[0]

    # ---- B. admin ------------------------------------------------------------
    await do(world, "B1 admin create user", "POST", "/api/admin/users", by=root,
             json={"username": "carol", "email": "carol@example.test", "password": "CNRY11-adminset-A1!"})
    await do(world, "B2 admin reset pw", "POST", f"/api/admin/users/{bob.id}/reset-password", by=root,
             json={"new_password": "CNRY12-reset-A1!"})
    r = await do(world, "B3 invite code", "POST", "/api/admin/invite-codes", by=root, json={"note": "CNRY13note"})
    if r is not None and r.status_code < 300:
        DYN["invite_code_full"] = r.json().get("code", "")
        await do(world, "B3b invite revoke", "DELETE", f"/api/admin/invite-codes/{r.json().get('id')}", by=root)
    r = await do(world, "B4 admin api key", "POST", "/api/admin/api-keys", by=root, json={"name": "adm", "user_id": bob.id})
    if r is not None and r.status_code < 300:
        DYN["admin_api_key_tail"] = (r.json().get("key", "") or "")[8:]
    r = await do(world, "B5 model provider", "POST", "/api/admin/model-providers", by=root,
                 json={"name": "prov", "base_url": "https://u:CNRY14pw@llm.example.test/v1?key=CNRY15q", "api_key": "CNRY16-provider-key"})
    pid = (r.json().get("id") if r is not None and r.status_code < 300 else None)
    if pid:
        await do(world, "B5b provider update", "PUT", f"/api/admin/model-providers/{pid}", by=root,
                 json={"api_key": "CNRY17-provider-key2", "base_url": "https://llm2.example.test/CNRY18seg/v1"})
    await do(world, "B6 model", "POST", "/api/admin/models", by=root,
             json={"name": "m", "model_name": "gpt-x", "base_url": "https://llm.example.test/v1", "api_key": "CNRY19-model-key"})
    await do(world, "B7 settings", "PATCH", "/api/admin/settings", by=root,
             json={"announcement_enabled": True, "announcement_text": "CNRY20 announcement text"})
    await do(world, "B8 sensitive word", "POST", "/api/admin/sensitive-words", by=root, json={"word": "CNRY21word"})
    await do(world, "B9 ip rule", "POST", "/api/admin/ip-rules", by=root,
             json={"ip_address": "198.51.100.9", "rule_type": "deny", "note": "CNRY22note"})
    await do(world, "B10 admin user email", "PATCH", f"/api/admin/users/{bob.id}", by=root,
             json={"email": "cnry23-bob-new@example.test"})
    with patch("fim_one.web.api.notifications.instance_channel") as ic:
        ch = MagicMock()
        ch.send_notice = AsyncMock(return_value=MagicMock(ok=False, error="POST https://hooks.example.test/CNRY24hook failed"))
        ic.return_value = ch
        await do(world, "B11 notif send", "POST", "/api/notifications/send", by=root,
                 json={"provider": "slack", "title": "CNRY25title", "body": "CNRY26body", "channel": "CNRY27chan"})
    await do(world, "B12 system logs", "GET", "/api/admin/system-logs?level=ERROR", by=root)

    # ---- C. orgs & channels ----------------------------------------------
    r = await do(world, "C1 org create", "POST", "/api/orgs", by=alice, json={"name": "Acme", "description": "CNRY30desc"})
    org_id = data(r).get("id") if r is not None else None
    if org_id:
        await do(world, "C2 org update", "PUT", f"/api/orgs/{org_id}", by=alice,
                 json={"description": "CNRY31desc2", "settings": {"sso_secret": "CNRY32sso"}, "icon": "https://x.example.test/i.png?sig=CNRY33sig"})
        await do(world, "C3 member add", "POST", f"/api/orgs/{org_id}/members", by=alice, json={"email": "octo@example.test", "role": "member"})
        r = await do(world, "C4 channel create", "POST", "/api/channels", by=alice,
                     json={"name": "ops", "type": "slack_webhook", "org_id": org_id,
                           "config": {"webhook_url": "https://hooks.slack.com/services/T0/B0/CNRY34hook"}})
        cid = (r.json().get("id") if r is not None and r.status_code < 300 else None)
        if cid:
            await do(world, "C5 channel update", "PATCH", f"/api/channels/{cid}", by=alice,
                     json={"config": {"webhook_url": "https://hooks.slack.com/services/T0/B0/CNRY35hook", "CNRY36keyname": "v"}})
            with patch("fim_one.web.api.channels.build_channel") as bc:
                chan = MagicMock()
                chan.send_notice = AsyncMock(return_value=MagicMock(ok=False, error="POST https://hooks.slack.com/services/T0/B0/CNRY35hook 404"))
                bc.return_value = chan
                await do(world, "C6 channel test", "POST", f"/api/channels/{cid}/test", by=alice)
        r = await do(world, "C7 feishu channel", "POST", "/api/channels", by=alice,
                     json={"name": "fs", "type": "feishu", "org_id": org_id,
                           "config": {"app_id": "cli_CNRY37appid", "app_secret": "CNRY38secret", "chat_id": "oc_CNRY39chat",
                                      "encrypt_key": "CNRY40enc", "verification_token": "CNRY41vt"}})

    # ---- D. connectors -----------------------------------------------------
    r = await do(world, "D1 connector create", "POST", "/api/connectors", by=alice,
                 json={"name": "=cmd|' /C calc'!A0", "base_url": "https://u:CNRY42pw@crm.example.test/api/CNRY43seg/" + _pad("CNRY95") + "/v1?key=CNRY44q",
                       "auth_type": "bearer", "auth_config": {"default_token": "CNRY45tok"}})
    conn = data(r).get("id") if r is not None else None
    if conn:
        await do(world, "D2 connector update", "PUT", f"/api/connectors/{conn}", by=alice,
                 json={"base_url": "https://api.telegram.org/bot123456789:" + _pad("AAHCNRY46", 35), "auth_type": "api_key",
                       "auth_config": {"default_key": "CNRY47key", "header": "X-CNRY48hdr"}, "allow_fallback": True})
        r = await do(world, "D3 action create", "POST", f"/api/connectors/{conn}/actions", by=alice,
                     json={"name": "send", "method": "GET", "path": "/v1/send?api_key=CNRY49actq&x=1",
                           "headers": {"Authorization": "Bearer CNRY50acthdr"}, "requires_confirmation": True})
        aid = data(r).get("id") if r is not None else None
        if aid:
            await do(world, "D4 action update", "PUT", f"/api/connectors/{conn}/actions/{aid}", by=alice,
                     json={"path": "/bot123456789:" + _pad("AAHCNRY51", 35) + "/sendMessage?token=CNRY52actq2", "requires_confirmation": False})
            await do(world, "D5 action delete", "DELETE", f"/api/connectors/{conn}/actions/{aid}", by=alice)
        await do(world, "D6 my credentials", "PUT", f"/api/connectors/{conn}/my-credentials", by=alice,
                 json={"api_key": "CNRY53cred", "token": "CNRY53cred"})
        await do(world, "D7 connector import", "POST", "/api/connectors/import", by=alice,
                 json={"name": "imp", "base_url": "https://imp.example.test", "auth_type": "bearer",
                       "auth_config": {"default_token": "CNRY54imp"},
                       "actions": [{"name": "list", "method": "GET", "path": "/x?key=CNRY55"}]})
    await world.add(Connector(id="db-1", user_id=alice.id, name="warehouse", type="database",
                              db_config={"driver": "postgresql", "host": "127.0.0.1", "port": 9, "database": "d",
                                         "username": "u", "password": "CNRY56dbpw"}))
    await do(world, "D8 db query", "POST", "/api/connectors/db-1/query", by=alice,
             json={"sql": "SELECT 'CNRY57sql' FROM staff WHERE ssn='CNRY58'"})
    await do(world, "D9 db query denied", "POST", "/api/connectors/db-1/query", by=alice,
             json={"sql": "DROP TABLE CNRY59tbl"})
    await do(world, "D10 db connector update", "PUT", "/api/connectors/db-1", by=alice,
             json={"db_config": {"driver": "postgresql", "host": "db.example.test", "port": 5432, "database": "d",
                                 "username": "u", "password": "CNRY60dbpw2"}})

    # ---- E. MCP -------------------------------------------------------------
    r = await do(world, "E1 mcp create http", "POST", "/api/mcp-servers", by=alice,
                 json={"name": "files", "transport": "streamable_http",
                       "url": "https://mcp.example.test/" + _pad("CNRY61") + "/mcp", "headers": {"X-Api-Key": "CNRY62hdr"}})
    sid = data(r).get("id") if r is not None else None
    if sid:
        await do(world, "E2 mcp update", "PUT", f"/api/mcp-servers/{sid}", by=alice,
                 json={"url": "https://mcp2.example.test/sse/" + _pad("CNRY63"), "env": {"TOKEN": "CNRY64env"}, "description": "CNRY65desc"})
        await do(world, "E3 mcp my creds", "PUT", f"/api/mcp-servers/{sid}/my-credentials", by=alice,
                 json={"headers": {"X-CNRY66name": "CNRY67val"}, "env": {"K": "CNRY68"}})
    with patch.dict(os.environ, {"ALLOW_STDIO_MCP": "true", "ALLOWED_STDIO_COMMANDS": "npx,uvx"}):
        r = await do(world, "E4 mcp stdio", "POST", "/api/mcp-servers", by=root,
                     json={"name": "stdio", "transport": "stdio", "command": "npx",
                           "args": ["-y", "srv", "--api-key", "CNRY69arg"], "env": {"API_KEY": "CNRY70env"},
                           "working_dir": "/srv/CNRY71dir"})
        sid2 = data(r).get("id") if r is not None else None
        if sid2:
            await do(world, "E5 mcp stdio update", "PUT", f"/api/mcp-servers/{sid2}", by=root,
                     json={"command": "/opt/CNRY72dir/npx", "working_dir": "/home/u/.secrets/CNRY73"})

    # ---- F. workflows -----------------------------------------------------------
    await world.add(Workflow(id="wf-1", user_id=alice.id, name="nightly", blueprint={"nodes": []}))
    with patch("fim_one.web.api.workflows._validate_webhook_url"):
        await do(world, "F1 wf webhook", "PUT", "/api/workflows/wf-1", by=alice,
                 json={"webhook_url": "https://hooks.example.test/CNRY74hook"})
    await do(world, "F2 wf env", "PUT", "/api/workflows/wf-1/env", by=alice,
             json={"env_vars": {"STRIPE_KEY": "CNRY75envval", "sk_live_CNRY76_pasted_into_name": "x"}})
    await do(world, "F3 wf schedule", "PUT", "/api/workflows/wf-1/schedule", by=alice,
             json={"cron": "*/15 * * * *", "enabled": True, "timezone": "UTC", "inputs": {"password": "CNRY77inp"}})
    r = await do(world, "F4 trigger key", "POST", "/api/workflows/wf-1/generate-api-key", by=alice)
    if r is not None and r.status_code < 300:
        full = data(r).get("api_key", "")
        DYN["trigger_key_full"] = full
        DYN["trigger_key_tail"] = full[12:]
    await do(world, "F5 trigger invalid header", "POST", "/api/workflows/trigger",
             headers={"Authorization": "Bearer wf_0000000000CNRY78invalidkeyvalue000000000000"}, json={"inputs": {"p": "CNRY79"}})
    refusals._throttle.reset()
    await do(world, "F6 trigger invalid path", "POST", "/api/workflows/trigger/CNRY80pwd?token=CNRY81q", json={"inputs": {}})
    refusals._throttle.reset()
    await do(world, "F6b trigger invalid short secret", "POST", "/api/workflows/trigger",
             headers={"Authorization": "Bearer CNRY8"}, json={"inputs": {}})
    await do(world, "F7 runs export", "GET", "/api/workflows/wf-1/runs/export", by=alice)
    await do(world, "F8 trigger key revoke", "DELETE", "/api/workflows/wf-1/api-key", by=alice)

    # ---- G. agents ------------------------------------------------------------------
    r = await do(world, "G1 agent create", "POST", "/api/agents", by=alice,
                 json={"name": "line1\nline2,\"q\" @SUM(1+1)", "instructions": "CNRY82 instructions"})
    agent_id = data(r).get("id") if r is not None else None
    if agent_id:
        await do(world, "G2 agent update", "PUT", f"/api/agents/{agent_id}", by=alice,
                 json={"sandbox_config": {"env": {"TOKEN": "CNRY83sbx"}}, "tool_categories": ["web", "CNRY84cat"],
                       "model_config_json": {"api_key": "CNRY85mk"}, "instructions": "CNRY86"})
    await world.add(Conversation(id="conv-1", user_id=alice.id, title="CNRY87 title", mode="react"))
    await do(world, "G3 conv export", "GET", "/api/conversations/conv-1/export?format=md", by=alice)

    # ---- H. a blocked address ---------------------------------------------------
    refusals._throttle.reset()
    with patch(
        "fim_one.web.ip_rules.RuleCache.get",
        AsyncMock(return_value=parse_rules([(CLIENT_IP, "deny")])),
    ):
        await do(world, "I1 blocked trigger path", "POST",
                 "/api/workflows/trigger/wf_CNRY88keyinpath00000000?token=CNRY89q", json={})
        refusals._throttle.reset()
        await do(world, "I2 blocked short key", "POST", "/api/workflows/trigger/CNRY90shortkey", json={})
        refusals._throttle.reset()
        await do(world, "I3 blocked path", "GET", "/api/auth/x/CNRY91/y%2FCNRY92",
                 headers={"User-Agent": "CNRY93-ua"})

    hits = await _hits(world, root)

    # The session did what it set out to do: these were all recorded.
    recorded = {event.action for event in await world.events()}
    expected = {
        "auth.register", "auth.password_change", "auth.two_factor_enable",
        "auth.backup_codes_regenerate", "auth.two_factor_disable", "api_key.create",
        "auth.sessions_revoke", "auth.username_change", "export.own_data", "user.create",
        "user.reset_password", "invite_code.create", "invite_code.revoke",
        "model_provider.create", "model_provider.update", "model.create", "settings.update",
        "sensitive_word.create", "ip_rule.create", "user.update", "notifications.instance_send",
        "org.create", "org.update", "org.member_add", "channel.create",
        "channel.update", "channel.test", "connector.create", "connector.update",
        "connector.action_create", "connector.action_update", "connector.action_delete",
        "connector.credential_set", "database.query", "mcp_server.create", "mcp_server.update",
        "mcp_server.credential_set", "workflow.update", "workflow.env_update",
        "workflow.schedule_update", "workflow.trigger_key_generate",
        "workflow.trigger_key_invalid", "export.workflow_runs", "workflow.trigger_key_revoke",
        "agent.create", "agent.update", "export.conversation", "security.ip_blocked",
    }
    assert expected - recorded == set(), f"the session no longer exercises {expected - recorded}"

    leaks = {marker: sorted(where) for marker, where in hits.items() if marker not in BY_DESIGN}
    assert leaks == {}, "secrets found in rows:\n" + "\n".join(
        f"  {marker}: {where}" for marker, where in sorted(leaks.items())
    )
    gone = sorted(set(BY_DESIGN) - set(hits))
    assert gone == [], f"listed as appearing by design and no longer found: {gone}"

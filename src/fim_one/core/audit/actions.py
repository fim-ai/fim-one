"""Every audit action name, registered in one place.

An action is ``domain.verb``. :func:`fim_one.core.audit.writer.record`
accepts only names listed here: under test an unknown name raises, in
production it is written and reported. Each name has an English label in
``frontend/messages/en/admin.json`` (``audit.actionLabels``, the dot
written as ``__``); ``tests/test_audit_action_labels.py`` holds the two
lists together.

:data:`ADMIN_ACTIONS` are the names the platform administration endpoints
have always written through ``write_audit`` as string literals. They are
registered as they are; nothing renames a name that existing rows carry.
"""

from __future__ import annotations

__all__ = ["ACTIONS", "ADMIN_ACTIONS", "is_registered"]

ADMIN_ACTIONS: tuple[str, ...] = (
    # Users and sessions
    "user.create",
    "user.delete",
    "user.enable",
    "user.disable",
    "user.grant_admin",
    "user.revoke_admin",
    "user.reset_password",
    "user.force_logout",
    "user.set_quota",
    "auth.force_logout_all",
    "invite_code.create",
    "invite_code.revoke",
    # Settings, billing, storage
    "settings.update",
    "billing.activate",
    "billing.enable",
    "billing.disable",
    "billing_plan.create",
    "billing_plan.update",
    "billing_plan.delete",
    "storage.cleanup_orphaned",
    "storage.clear_user",
    "announcement.create",
    "announcement.update",
    "announcement.delete",
    "notifications.config_update",
    "notifications.test",
    # Models
    "model.create",
    "model.update",
    "model.delete",
    "model.enable",
    "model.disable",
    "model.set_role",
    "model_config.export",
    "model_config.import",
    "model_group.create",
    "model_group.update",
    "model_group.delete",
    "model_group.activate",
    "model_group.deactivate_all",
    "model_provider.create",
    "model_provider.update",
    "model_provider.delete",
    "model_provider_model.create",
    "model_provider_model.update",
    "model_provider_model.delete",
    "model_provider_model.batch_delete",
    # Security
    "ip_rule.create",
    "ip_rule.delete",
    "ip_rule.enable",
    "ip_rule.disable",
    "ip_rule.batch_import",
    "sensitive_word.create",
    "sensitive_word.delete",
    "sensitive_word.toggle",
    "sensitive_word.batch_import",
    "api_key.create",
    "api_key.delete",
    "api_key.enable",
    "api_key.disable",
    "credential.admin_revoke",
    # Other people's data
    "conversation.viewed",
    "conversation.delete",
    "export.users",
    "export.conversations",
    "export.full_backup",
    # Resources
    "agent.admin_delete",
    "agent.admin_batch_delete",
    "agent.admin_batch_toggle",
    "connector.admin_batch_delete",
    "connector.admin_batch_toggle",
    "kb.admin_delete",
    "kb.admin_batch_delete",
    "kb.admin_batch_toggle",
    "skill.admin_delete",
    "skill.admin_enable",
    "skill.admin_disable",
    "skill.admin_batch_delete",
    "skill.admin_batch_toggle",
    "workflow.admin_delete",
    "workflow.admin_toggle",
    "workflow.admin_batch_delete",
    "workflow.admin_batch_publish",
    "workflow.admin_batch_toggle",
    "workflow.cleanup_runs",
    "workflow_template.create",
    "workflow_template.update",
    "workflow_template.delete",
    "schedule.admin_pause",
    "schedule.admin_resume",
    "eval_dataset.admin_delete",
    "eval_run.admin_delete",
    "eval_run.admin_cleanup",
    # Reviews and the market
    "review.admin_batch_approve",
    "review.admin_batch_reject",
    "market.resource_unpublish",
    "market.resource_delete",
)

# A user deleting their own account.
ACCOUNT_SELF_DELETE = "account.self_delete"

# -- Signing in ---------------------------------------------------------------
# Sign-in attempts themselves are rows of ``login_history`` (with their
# method), not events here.
# A first sign-in through an OAuth provider created the account.
AUTH_OAUTH_SIGNUP = "auth.oauth_signup"

# -- A user's own account -----------------------------------------------------
# The target is the user; the record never holds a password, a code or a key.
AUTH_REGISTER = "auth.register"
# The first-time setup that creates the instance's first administrator.
AUTH_SETUP_ADMIN = "auth.setup_admin"
AUTH_PASSWORD_CHANGE = "auth.password_change"
# A first password, set by an account that had only a provider sign-in.
AUTH_PASSWORD_SET = "auth.password_set"
# By emailed code, signed in or not.
AUTH_PASSWORD_RESET = "auth.password_reset"
AUTH_TWO_FACTOR_ENABLE = "auth.two_factor_enable"
AUTH_TWO_FACTOR_DISABLE = "auth.two_factor_disable"
AUTH_BACKUP_CODES_REGENERATE = "auth.backup_codes_regenerate"
AUTH_EMAIL_CHANGE = "auth.email_change"
AUTH_OAUTH_BIND = "auth.oauth_bind"
AUTH_OAUTH_UNBIND = "auth.oauth_unbind"
# The user signed every session of theirs out.
AUTH_SESSIONS_REVOKE = "auth.sessions_revoke"
# A user's own API keys are recorded under the administrators' names for
# the same thing: ``api_key.create`` / ``enable`` / ``disable`` / ``delete``.

# -- Organizations and their members -----------------------------------------
# ``org_id`` is the organization; for a membership the target is the member
# and the change is the role before and after.
ORG_CREATE = "org.create"
ORG_UPDATE = "org.update"
ORG_DELETE = "org.delete"
ORG_MEMBER_ADD = "org.member_add"
ORG_MEMBER_ROLE_CHANGE = "org.member_role_change"
# Removed by an administrator of the organization, or left (detail ``left``).
ORG_MEMBER_REMOVE = "org.member_remove"

# -- Channels and credentials -------------------------------------------------
# No record holds a credential: a config key or a secret field says only
# that it changed.
CHANNEL_CREATE = "channel.create"
CHANNEL_UPDATE = "channel.update"
CHANNEL_DELETE = "channel.delete"
# A test message was sent through the channel.
CHANNEL_TEST = "channel.test"
# A user's personal credential for a connector or an MCP server.
CONNECTOR_CREDENTIAL_SET = "connector.credential_set"
CONNECTOR_CREDENTIAL_DELETE = "connector.credential_delete"
MCP_SERVER_CREDENTIAL_SET = "mcp_server.credential_set"
MCP_SERVER_CREDENTIAL_DELETE = "mcp_server.credential_delete"
# The encrypted env vars of a workflow were replaced.
WORKFLOW_ENV_UPDATE = "workflow.env_update"

ACTIONS: frozenset[str] = frozenset(ADMIN_ACTIONS) | frozenset(
    value
    for name, value in dict(globals()).items()
    if name.isupper() and not name.startswith("_") and isinstance(value, str)
)


def is_registered(action: str) -> bool:
    """Whether *action* is a registered audit action name."""
    return action in ACTIONS

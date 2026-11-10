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

ACTIONS: frozenset[str] = frozenset(ADMIN_ACTIONS) | frozenset(
    value
    for name, value in dict(globals()).items()
    if name.isupper() and not name.startswith("_") and isinstance(value, str)
)


def is_registered(action: str) -> bool:
    """Whether *action* is a registered audit action name."""
    return action in ACTIONS

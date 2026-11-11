"""Which state-changing routes leave an audit record.

Every route of the application that changes something (POST, PUT, PATCH,
DELETE) is in exactly one of three tables:

- :data:`AUDITED`: the request leaves a record. The value is what is
  written: audit action names (``core/audit/actions.py``),
  ``review_log:<x>`` for an entry in an organization's review trail,
  :data:`LOGIN` for a sign-in attempt in ``login_history``.
- :data:`EXEMPT`: it is not a security action, for one of the reasons in
  :data:`EXEMPT_REASONS`.
- :data:`KNOWN_GAPS`: it should leave a record and does not yet. Each entry
  says which piece of work will add it.

``test_coverage.py`` holds the tables against the routes the application
actually has: a new route that is in none of them fails the test, and so
does an entry for a route that no longer exists.

Two more tables account for records that no state-changing route writes:
:data:`AUDITED_READS` (GET routes that record an export, a view or a
sign-in through a redirect) and :data:`NON_ROUTE_ACTIONS` (middleware,
background jobs, shared helpers), each of the latter pointing at the test
that shows the record being written.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

Route = tuple[str, str]
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# A sign-in attempt, written to ``login_history``.
LOGIN = "auth.login"
# An entry in an organization's review trail (``review_log``), by its action.
REVIEW_LOG = "review_log:"
REVIEW_ACTIONS = frozenset({"submitted", "resubmitted", "unpublished", "approved", "rejected"})

# Why a state-changing route leaves no audit record.
CONTENT = "content"
MODEL_CALL = "model_call"
PROBE = "probe"
CODE_DELIVERY = "code_delivery"
SESSION = "session"
PAYMENT_REDIRECT = "payment_redirect"
DISABLED = "disabled"
EXEMPT_REASONS: dict[str, str] = {
    CONTENT: (
        "edits or removes the caller's own content (a conversation, a document, a "
        "profile field, an annotation, an answer to the agent's question); who may "
        "do what is not changed"
    ),
    MODEL_CALL: "runs a model for the caller; what it costs is the usage ledger's to record",
    PROBE: (
        "tests, validates or looks something up, or sends a test message; nothing "
        "that is kept changes"
    ),
    CODE_DELIVERY: "sends a one-time code; the action the code confirms is the one recorded",
    SESSION: "session plumbing: renews a token or hands out a short-lived ticket",
    DISABLED: (
        "switched off: answers 501 before doing anything. Its record is in place for "
        "when it returns, and it then moves to AUDITED with a behaviour test"
    ),
    PAYMENT_REDIRECT: (
        "opens a payment page at the processor; the plan change is recorded when the "
        "processor reports it"
    ),
}


class Gap(NamedTuple):
    """Something that should leave a record and does not yet."""

    # Which piece of work adds it.
    owner: str
    # What it will record.
    action: str
    # Why it is left to that work.
    reason: str = ""


# Work outside this branch that a gap may be left to, with the reason.
# Nothing else may own a gap.
DEFERRED_OWNERS: dict[str, str] = {
    "T10 P7": (
        "workflow run events (a run started, refused, cancelled or closed) are "
        "recorded in the run service, where every way of starting a run passes: one "
        "record per run whatever the entry, with the run id. A record in each route "
        "would miss the schedule and the agent tool and would have to be removed again."
    ),
}


def all_routes(app: Any) -> Iterator[tuple[Route, Any]]:
    """Every (method, path) of *app*, included routers walked, with its endpoint function."""

    def walk(routes: Any, prefix: str) -> Iterator[tuple[Route, Any]]:
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                # FastAPI wraps each included router; its routes carry their
                # own paths and the wrapper the prefix it was included under.
                context = getattr(route, "include_context", None)
                yield from walk(included.routes, prefix + (getattr(context, "prefix", "") or ""))
            elif getattr(route, "methods", None) and hasattr(route, "endpoint"):
                for method in sorted(route.methods):
                    yield (method, prefix + route.path), route.endpoint

    yield from walk(app.routes, "")


def mutating_routes(app: Any) -> Iterator[tuple[Route, Any]]:
    """Every (method, path) of *app* that changes state, with its endpoint function."""
    for route, endpoint in all_routes(app):
        if route[0] in MUTATING:
            yield route, endpoint


AUDITED: dict[Route, tuple[str, ...]] = {
    ("POST", "/api/builder/session"): ("agent.create",),
    ("POST", "/api/workflows/{workflow_id}/test-webhook"): ("workflow.webhook_test",),
    ("PATCH", "/api/auth/profile"): ("auth.username_change",),
    ("POST", "/api/channels/{channel_id}/test-approval"): ("channel.test_approval",),
    ("POST", "/api/admin/actions/force-logout-all"): ("auth.force_logout_all",),
    ("POST", "/api/admin/agents/batch-delete"): ("agent.admin_batch_delete",),
    ("POST", "/api/admin/agents/batch-toggle"): ("agent.admin_batch_toggle",),
    ("DELETE", "/api/admin/agents/{agent_id}"): ("agent.admin_delete",),
    ("POST", "/api/admin/announcements"): ("announcement.create",),
    ("DELETE", "/api/admin/announcements/{ann_id}"): ("announcement.delete",),
    ("PUT", "/api/admin/announcements/{ann_id}"): ("announcement.update",),
    ("POST", "/api/admin/api-keys"): ("api_key.create",),
    ("DELETE", "/api/admin/api-keys/{key_id}"): ("api_key.delete",),
    ("PATCH", "/api/admin/api-keys/{key_id}/active"): ("api_key.disable", "api_key.enable"),
    ("POST", "/api/admin/billing/plans"): ("billing_plan.create",),
    ("DELETE", "/api/admin/billing/plans/{plan_id}"): ("billing_plan.delete",),
    ("PATCH", "/api/admin/billing/plans/{plan_id}"): ("billing_plan.update",),
    ("POST", "/api/admin/connectors/batch-delete"): ("connector.admin_batch_delete",),
    ("POST", "/api/admin/connectors/batch-toggle"): ("connector.admin_batch_toggle",),
    ("DELETE", "/api/admin/conversations/{conv_id}"): ("conversation.delete",),
    ("DELETE", "/api/admin/credentials/connector/{credential_id}"): ("credential.admin_revoke",),
    ("DELETE", "/api/admin/credentials/mcp/{credential_id}"): ("credential.admin_revoke",),
    ("POST", "/api/admin/eval/cleanup"): ("eval_run.admin_cleanup",),
    ("DELETE", "/api/admin/eval/datasets/{dataset_id}"): ("eval_dataset.admin_delete",),
    ("DELETE", "/api/admin/eval/runs/{run_id}"): ("eval_run.admin_delete",),
    ("POST", "/api/admin/invite-codes"): ("invite_code.create",),
    ("DELETE", "/api/admin/invite-codes/{code_id}"): ("invite_code.revoke",),
    ("POST", "/api/admin/ip-rules"): ("ip_rule.create",),
    ("POST", "/api/admin/ip-rules/batch-import"): ("ip_rule.batch_import",),
    ("DELETE", "/api/admin/ip-rules/{rule_id}"): ("ip_rule.delete",),
    ("PATCH", "/api/admin/ip-rules/{rule_id}/active"): ("ip_rule.disable", "ip_rule.enable"),
    ("POST", "/api/admin/knowledge-bases/batch-delete"): ("kb.admin_batch_delete",),
    ("POST", "/api/admin/knowledge-bases/batch-toggle"): ("kb.admin_batch_toggle",),
    ("DELETE", "/api/admin/knowledge-bases/{kb_id}"): ("kb.admin_delete",),
    ("DELETE", "/api/admin/market/resources/{resource_type}/{resource_id}"): (
        "market.resource_delete",
    ),
    ("PATCH", "/api/admin/market/resources/{resource_type}/{resource_id}/unpublish"): (
        "market.resource_unpublish",
    ),
    ("POST", "/api/admin/model-config/import"): ("model_config.import",),
    ("POST", "/api/admin/model-groups"): ("model_group.create",),
    ("POST", "/api/admin/model-groups/deactivate"): ("model_group.deactivate_all",),
    ("DELETE", "/api/admin/model-groups/{group_id}"): ("model_group.delete",),
    ("PUT", "/api/admin/model-groups/{group_id}"): ("model_group.update",),
    ("PATCH", "/api/admin/model-groups/{group_id}/activate"): ("model_group.activate",),
    ("POST", "/api/admin/model-provider-models/batch-delete"): (
        "model_provider_model.batch_delete",
    ),
    ("DELETE", "/api/admin/model-provider-models/{model_id}"): ("model_provider_model.delete",),
    ("PUT", "/api/admin/model-provider-models/{model_id}"): ("model_provider_model.update",),
    ("POST", "/api/admin/model-providers"): ("model_provider.create",),
    ("DELETE", "/api/admin/model-providers/{provider_id}"): ("model_provider.delete",),
    ("PUT", "/api/admin/model-providers/{provider_id}"): ("model_provider.update",),
    ("POST", "/api/admin/model-providers/{provider_id}/models"): ("model_provider_model.create",),
    ("POST", "/api/admin/models"): ("model.create",),
    ("DELETE", "/api/admin/models/{model_id}"): ("model.delete",),
    ("PUT", "/api/admin/models/{model_id}"): ("model.update",),
    ("PATCH", "/api/admin/models/{model_id}/active"): ("model.disable", "model.enable"),
    ("PATCH", "/api/admin/models/{model_id}/role"): ("model.set_role",),
    ("PUT", "/api/admin/notifications/config"): ("notifications.config_update",),
    ("POST", "/api/admin/notifications/test"): ("notifications.test",),
    ("POST", "/api/admin/reviews/batch-approve"): (
        "review.admin_batch_approve",
        "review_log:approved",
    ),
    ("POST", "/api/admin/reviews/batch-reject"): (
        "review.admin_batch_reject",
        "review_log:rejected",
    ),
    ("PATCH", "/api/admin/schedules/{workflow_id}/active"): (
        "schedule.admin_pause",
        "schedule.admin_resume",
    ),
    ("POST", "/api/admin/sensitive-words"): ("sensitive_word.create",),
    ("POST", "/api/admin/sensitive-words/batch"): ("sensitive_word.batch_import",),
    ("DELETE", "/api/admin/sensitive-words/{word_id}"): ("sensitive_word.delete",),
    ("PUT", "/api/admin/sensitive-words/{word_id}/toggle"): ("sensitive_word.toggle",),
    ("PATCH", "/api/admin/settings"): ("settings.update",),
    ("POST", "/api/admin/skills/batch-delete"): ("skill.admin_batch_delete",),
    ("POST", "/api/admin/skills/batch-toggle"): ("skill.admin_batch_toggle",),
    ("DELETE", "/api/admin/skills/{skill_id}"): ("skill.admin_delete",),
    ("PATCH", "/api/admin/skills/{skill_id}/active"): ("skill.admin_disable", "skill.admin_enable"),
    ("DELETE", "/api/admin/storage/orphaned"): ("storage.cleanup_orphaned",),
    ("DELETE", "/api/admin/storage/user/{user_id}"): ("storage.clear_user",),
    ("POST", "/api/admin/system/billing/activate"): ("billing.activate",),
    ("POST", "/api/admin/system/billing/toggle"): ("billing.disable", "billing.enable"),
    ("POST", "/api/admin/users"): ("user.create",),
    ("DELETE", "/api/admin/users/{user_id}"): ("user.delete",),
    ("PATCH", "/api/admin/users/{user_id}/active"): ("user.disable", "user.enable"),
    ("PATCH", "/api/admin/users/{user_id}/admin"): ("user.grant_admin", "user.revoke_admin"),
    ("POST", "/api/admin/users/{user_id}/force-logout"): ("user.force_logout",),
    ("PATCH", "/api/admin/users/{user_id}/quota"): ("user.set_quota",),
    ("POST", "/api/admin/users/{user_id}/reset-password"): ("user.reset_password",),
    ("POST", "/api/admin/workflow-templates"): ("workflow_template.create",),
    ("DELETE", "/api/admin/workflow-templates/{template_id}"): ("workflow_template.delete",),
    ("PUT", "/api/admin/workflow-templates/{template_id}"): ("workflow_template.update",),
    ("POST", "/api/admin/workflows/batch-delete"): ("workflow.admin_batch_delete",),
    ("POST", "/api/admin/workflows/batch-publish"): ("workflow.admin_batch_publish",),
    ("POST", "/api/admin/workflows/batch-toggle"): ("workflow.admin_batch_toggle",),
    ("POST", "/api/admin/workflows/cleanup-runs"): ("workflow.cleanup_runs",),
    ("DELETE", "/api/admin/workflows/{workflow_id}"): ("workflow.admin_delete",),
    ("POST", "/api/admin/workflows/{workflow_id}/toggle"): ("workflow.admin_toggle",),
    ("POST", "/api/agent-templates/{template_id}/create"): ("agent.create",),
    ("POST", "/api/agents"): ("agent.create",),
    ("POST", "/api/agents/ai/create"): ("agent.create",),
    ("DELETE", "/api/agents/{agent_id}"): ("agent.delete",),
    ("PUT", "/api/agents/{agent_id}"): ("agent.update",),
    ("POST", "/api/agents/{agent_id}/ai/refine"): ("agent.update",),
    ("POST", "/api/agents/{agent_id}/fork"): ("agent.create",),
    ("POST", "/api/agents/{agent_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/agents/{agent_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/agents/{agent_id}/unpublish"): (
        "review_log:unpublished",
        "agent.unpublish_by_admin",
    ),
    ("POST", "/api/auth/2fa/backup-codes"): ("auth.backup_codes_regenerate",),
    ("POST", "/api/auth/2fa/disable"): ("auth.two_factor_disable",),
    ("POST", "/api/auth/2fa/enable"): ("auth.two_factor_enable",),
    ("DELETE", "/api/auth/account"): ("account.self_delete",),
    ("POST", "/api/auth/change-password"): ("auth.password_change",),
    ("POST", "/api/auth/forgot-password"): ("auth.password_reset",),
    ("POST", "/api/auth/login"): (LOGIN,),
    ("POST", "/api/auth/login-with-code"): (LOGIN,),
    ("POST", "/api/auth/login/verify-2fa"): (LOGIN,),
    ("DELETE", "/api/auth/oauth-bindings/{provider}"): ("auth.oauth_unbind",),
    ("POST", "/api/auth/register"): ("auth.register",),
    ("POST", "/api/auth/reset-password"): ("auth.password_reset",),
    ("POST", "/api/auth/set-password"): ("auth.password_set",),
    ("POST", "/api/auth/setup"): ("auth.setup_admin",),
    ("POST", "/api/channels"): ("channel.create",),
    ("DELETE", "/api/channels/{channel_id}"): ("channel.delete",),
    ("PATCH", "/api/channels/{channel_id}"): ("channel.update",),
    ("POST", "/api/channels/{channel_id}/test"): ("channel.test",),
    ("POST", "/api/connector-templates/{template_id}/create"): ("connector.create",),
    ("POST", "/api/connectors"): ("connector.create",),
    ("POST", "/api/connectors/ai/create"): ("connector.create",),
    ("POST", "/api/connectors/from-config"): ("connector.create",),
    ("POST", "/api/connectors/import"): ("connector.create",),
    ("POST", "/api/connectors/import-openapi"): ("connector.create",),
    ("DELETE", "/api/connectors/{connector_id}"): ("connector.delete",),
    ("PUT", "/api/connectors/{connector_id}"): ("connector.update",),
    ("POST", "/api/connectors/{connector_id}/actions"): ("connector.action_create",),
    ("DELETE", "/api/connectors/{connector_id}/actions/{action_id}"): ("connector.action_delete",),
    ("PUT", "/api/connectors/{connector_id}/actions/{action_id}"): ("connector.action_update",),
    ("POST", "/api/connectors/{connector_id}/ai/generate-actions"): ("connector.actions_import",),
    ("POST", "/api/connectors/{connector_id}/ai/refine-action"): (
        "connector.actions_import",
        "connector.update",
    ),
    ("POST", "/api/connectors/{connector_id}/fork"): ("connector.create",),
    ("POST", "/api/connectors/{connector_id}/import-openapi"): ("connector.actions_import",),
    ("DELETE", "/api/connectors/{connector_id}/my-credentials"): ("connector.credential_delete",),
    ("PUT", "/api/connectors/{connector_id}/my-credentials"): ("connector.credential_set",),
    ("POST", "/api/connectors/{connector_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/connectors/{connector_id}/resubmit"): ("review_log:resubmitted",),
    ("PUT", "/api/connectors/{connector_id}/schemas/bulk"): ("connector.schema_fence_update",),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}"): (
        "connector.schema_fence_update",
    ),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}/columns/{col_id}"): (
        "connector.schema_fence_update",
    ),
    ("POST", "/api/connectors/{connector_id}/toggle"): ("connector.update",),
    ("POST", "/api/connectors/{connector_id}/unpublish"): (
        "review_log:unpublished",
        "connector.unpublish_by_admin",
    ),
    ("POST", "/api/knowledge-bases"): ("kb.create",),
    ("DELETE", "/api/knowledge-bases/{kb_id}"): ("kb.delete",),
    ("PUT", "/api/knowledge-bases/{kb_id}"): ("kb.update",),
    ("POST", "/api/knowledge-bases/{kb_id}/toggle"): ("kb.update",),
    ("POST", "/api/market/subscribe"): ("market.subscribe",),
    ("DELETE", "/api/market/unsubscribe"): ("market.unsubscribe",),
    ("POST", "/api/mcp-servers"): ("mcp_server.create",),
    ("DELETE", "/api/mcp-servers/{server_id}"): ("mcp_server.delete",),
    ("PUT", "/api/mcp-servers/{server_id}"): ("mcp_server.update",),
    ("POST", "/api/mcp-servers/{server_id}/fork"): ("mcp_server.create",),
    ("PUT", "/api/mcp-servers/{server_id}/my-credentials"): (
        "mcp_server.credential_set",
        "mcp_server.credential_delete",
    ),
    ("POST", "/api/mcp-servers/{server_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/mcp-servers/{server_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/mcp-servers/{server_id}/unpublish"): (
        "review_log:unpublished",
        "mcp_server.unpublish_by_admin",
    ),
    ("POST", "/api/me/api-keys"): ("api_key.create",),
    ("DELETE", "/api/me/api-keys/{key_id}"): ("api_key.delete",),
    ("PATCH", "/api/me/api-keys/{key_id}/active"): ("api_key.enable", "api_key.disable"),
    ("POST", "/api/me/sessions/revoke-all"): ("auth.sessions_revoke",),
    ("POST", "/api/orgs"): ("org.create",),
    ("DELETE", "/api/orgs/{org_id}"): ("org.delete",),
    ("PUT", "/api/orgs/{org_id}"): ("org.update",),
    ("POST", "/api/orgs/{org_id}/members"): ("org.member_add",),
    ("DELETE", "/api/orgs/{org_id}/members/{user_id}"): ("org.member_remove",),
    ("PATCH", "/api/orgs/{org_id}/members/{user_id}"): ("org.member_role_change",),
    ("POST", "/api/orgs/{org_id}/reviews/approve"): ("review_log:approved",),
    ("POST", "/api/orgs/{org_id}/reviews/reject"): ("review_log:rejected",),
    ("POST", "/api/skill-templates/{template_id}/create"): ("skill.create",),
    ("POST", "/api/skills"): ("skill.create",),
    ("DELETE", "/api/skills/{skill_id}"): ("skill.delete",),
    ("PUT", "/api/skills/{skill_id}"): ("skill.update",),
    ("POST", "/api/skills/{skill_id}/fork"): ("skill.create",),
    ("POST", "/api/skills/{skill_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/skills/{skill_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/skills/{skill_id}/toggle"): ("skill.update",),
    ("POST", "/api/skills/{skill_id}/unpublish"): (
        "review_log:unpublished",
        "skill.unpublish_by_admin",
    ),
    ("POST", "/api/workflow-templates/{template_id}/create-workflow"): ("workflow.create",),
    ("POST", "/api/workflows"): ("workflow.create",),
    ("POST", "/api/workflows/from-template"): ("workflow.create",),
    ("POST", "/api/workflows/import"): ("workflow.create",),
    ("DELETE", "/api/workflows/{workflow_id}"): ("workflow.delete",),
    ("PUT", "/api/workflows/{workflow_id}"): ("workflow.update",),
    ("DELETE", "/api/workflows/{workflow_id}/api-key"): ("workflow.trigger_key_revoke",),
    ("PUT", "/api/workflows/{workflow_id}/env"): ("workflow.env_update",),
    ("POST", "/api/workflows/{workflow_id}/fork"): ("workflow.create",),
    ("POST", "/api/workflows/{workflow_id}/generate-api-key"): ("workflow.trigger_key_generate",),
    ("POST", "/api/workflows/{workflow_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/workflows/{workflow_id}/resubmit"): ("review_log:resubmitted",),
    ("DELETE", "/api/workflows/{workflow_id}/runs"): ("workflow.runs_clear",),
    ("DELETE", "/api/workflows/{workflow_id}/runs/{run_id}"): ("workflow.run_delete",),
    ("DELETE", "/api/workflows/{workflow_id}/schedule"): ("workflow.schedule_delete",),
    ("PUT", "/api/workflows/{workflow_id}/schedule"): ("workflow.schedule_update",),
    ("POST", "/api/workflows/{workflow_id}/unpublish"): (
        "review_log:unpublished",
        "workflow.unpublish_by_admin",
    ),
    ("POST", "/api/webhooks/stripe"): ("billing.plan_change",),
    ("POST", "/api/connectors/{connector_id}/query"): ("database.query",),
    ("POST", "/api/admin/market/import-templates"): ("market.import_templates",),
    ("DELETE", "/api/admin/organizations/{org_id}"): ("org.admin_delete",),
    ("PUT", "/api/admin/organizations/{org_id}"): ("org.admin_update",),
    ("PATCH", "/api/admin/users/{user_id}"): ("user.update",),
    ("POST", "/api/notifications/send"): ("notifications.instance_send",),
    ("POST", "/api/notifications/test"): ("notifications.instance_test",),
    ("POST", "/api/channels/{channel_id}/callback"): ("confirmation.approve", "confirmation.reject"),
    ("POST", "/api/confirmations/{confirmation_id}/respond"): ("confirmation.approve", "confirmation.reject"),
}

EXEMPT: dict[Route, str] = {
    # content
    ("DELETE", "/api/auth/avatar"): CONTENT,
    ("POST", "/api/auth/avatar"): CONTENT,
    ("POST", "/api/confirmations/{confirmation_id}/answer"): CONTENT,
    ("POST", "/api/conversations"): CONTENT,
    ("DELETE", "/api/conversations/batch"): CONTENT,
    ("DELETE", "/api/conversations/{conversation_id}"): CONTENT,
    ("PATCH", "/api/conversations/{conversation_id}"): CONTENT,
    ("POST", "/api/eval/datasets"): CONTENT,
    ("DELETE", "/api/eval/datasets/{dataset_id}"): CONTENT,
    ("PUT", "/api/eval/datasets/{dataset_id}"): CONTENT,
    ("POST", "/api/eval/datasets/{dataset_id}/cases"): CONTENT,
    ("DELETE", "/api/eval/datasets/{dataset_id}/cases/{case_id}"): CONTENT,
    ("PUT", "/api/eval/datasets/{dataset_id}/cases/{case_id}"): CONTENT,
    ("DELETE", "/api/eval/runs/{run_id}"): CONTENT,
    ("POST", "/api/files/upload"): CONTENT,
    ("DELETE", "/api/files/{file_id}"): CONTENT,
    ("DELETE", "/api/knowledge-bases/{kb_id}/chunks/{chunk_id}"): CONTENT,
    ("PUT", "/api/knowledge-bases/{kb_id}/chunks/{chunk_id}"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents/create"): CONTENT,
    ("DELETE", "/api/knowledge-bases/{kb_id}/documents/{doc_id}"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents/{doc_id}/retry"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/import-urls"): CONTENT,
    ("PUT", "/api/me/notifications"): CONTENT,
    ("POST", "/api/workflows/{workflow_id}/versions/{version_id}/restore"): CONTENT,
    # model_call
    ("POST", "/api/auto"): MODEL_CALL,
    ("POST", "/api/chat/inject"): MODEL_CALL,
    ("POST", "/api/chat/inject/recall"): MODEL_CALL,
    ("POST", "/api/chat/resume"): MODEL_CALL,
    ("POST", "/api/connectors/{connector_id}/ai/annotate"): MODEL_CALL,
    ("POST", "/api/connectors/{connector_id}/ai/db-chat"): MODEL_CALL,
    ("POST", "/api/dag"): MODEL_CALL,
    ("POST", "/api/eval/runs"): MODEL_CALL,
    ("POST", "/api/knowledge-bases/{kb_id}/ai/chat"): MODEL_CALL,
    ("POST", "/api/react"): MODEL_CALL,
    # disabled
    # ``auth.email_change`` is written by its code, which nothing reaches.
    ("POST", "/api/auth/change-email/confirm"): DISABLED,
    # probe
    # Named a toggle, it changes nothing: it returns the agent as it is.
    ("PATCH", "/api/admin/agents/{agent_id}/active"): PROBE,
    ("POST", "/api/admin/sensitive-words/check"): PROBE,
    ("POST", "/api/auth/2fa/setup"): PROBE,
    ("POST", "/api/auth/verify-forgot-code"): PROBE,
    ("POST", "/api/channels/discover-chats"): PROBE,
    ("POST", "/api/connectors/test-connection"): PROBE,
    ("POST", "/api/connectors/{connector_id}/circuit-breaker-reset"): PROBE,
    ("POST", "/api/connectors/{connector_id}/introspect"): PROBE,
    ("POST", "/api/connectors/{connector_id}/test-connection"): PROBE,
    ("POST", "/api/knowledge-bases/{kb_id}/retrieve"): PROBE,
    ("POST", "/api/mcp-servers/{server_id}/test"): PROBE,
    ("POST", "/api/workflows/validate"): PROBE,
    ("POST", "/api/workflows/{workflow_id}/validate"): PROBE,
    # code_delivery
    ("POST", "/api/auth/change-email/request"): CODE_DELIVERY,
    ("POST", "/api/auth/send-forgot-code"): CODE_DELIVERY,
    ("POST", "/api/auth/send-login-code"): CODE_DELIVERY,
    ("POST", "/api/auth/send-reset-code"): CODE_DELIVERY,
    ("POST", "/api/auth/send-verification-code"): CODE_DELIVERY,
    # session
    ("POST", "/api/auth/oauth/bind-ticket"): SESSION,
    ("POST", "/api/auth/refresh"): SESSION,
    ("POST", "/api/chat/ticket"): SESSION,
    # payment_redirect
    ("POST", "/api/billing/checkout"): PAYMENT_REDIRECT,
    ("POST", "/api/billing/portal"): PAYMENT_REDIRECT,
}

KNOWN_GAPS: dict[Route, Gap] = {
    # T10 P7: workflow run events. What the request changes is a run, and
    # the run row records it today (who, which entry, the outcome) for as
    # long as run retention keeps it.
    ("POST", "/api/workflows/{workflow_id}/run"): Gap(
        "T10 P7",
        "workflow.run_started / workflow.run_refused",
        "starts a run by hand; recorded once in the run service with every other entry",
    ),
    ("POST", "/api/workflows/{workflow_id}/batch-run"): Gap(
        "T10 P7",
        "workflow.run_started / workflow.run_refused",
        "starts one run per item; each is a run event of the run service",
    ),
    ("POST", "/api/workflows/trigger"): Gap(
        "T10 P7",
        "workflow.run_started / workflow.run_refused",
        "starts a run for a trigger key; the run event carries the key's prefix "
        "(an unknown key is already recorded: workflow.trigger_key_invalid)",
    ),
    ("POST", "/api/workflows/trigger/{api_key}"): Gap(
        "T10 P7",
        "workflow.run_started / workflow.run_refused",
        "the deprecated spelling of the trigger route; same run event",
    ),
    ("POST", "/api/workflows/{workflow_id}/test-node"): Gap(
        "T10 P7",
        "workflow.run_started / workflow.run_refused",
        "runs one step for real (its connector or MCP call is in the call log); "
        "a run event like any other start",
    ),
    ("POST", "/api/workflows/{workflow_id}/runs/{run_id}/cancel"): Gap(
        "T10 P7",
        "workflow.run_cancelled",
        "ends a run; recorded with the other ways a run ends (failed, timed out, "
        "closed as stale)",
    ),
}


# GET routes that leave a record: an export, a look at someone else's data.
AUDITED_READS: dict[Route, tuple[str, ...]] = {
    ("GET", "/api/admin/audit-log/export"): ("export.audit_log",),
    ("GET", "/api/admin/conversations/{conv_id}/messages"): ("conversation.viewed",),
    ("GET", "/api/admin/export/conversations"): ("export.conversations",),
    ("GET", "/api/admin/export/full-backup"): ("export.full_backup",),
    ("GET", "/api/admin/export/users"): ("export.users",),
    ("GET", "/api/admin/model-config/export"): ("model_config.export",),
    ("GET", "/api/admin/system-logs"): ("system_logs.read",),
    ("GET", "/api/auth/me/export"): ("export.own_data",),
    ("GET", "/api/conversations/{conversation_id}/export"): ("export.conversation",),
    ("GET", "/api/workflows/{workflow_id}/runs/export"): ("export.workflow_runs",),
    # A sign-in through a provider's redirect, the account it may create,
    # and the linking of a provider to a signed-in account.
    ("GET", "/api/auth/oauth/{provider_name}/callback"): (
        LOGIN,
        "auth.oauth_signup",
        "auth.oauth_bind",
    ),
}

# Read routes and other entry points that should leave a record and do not yet.
KNOWN_NON_ROUTE_GAPS: dict[str, Gap] = {
    # Workflow run events: who started which run through which entry, and
    # the runs the platform refused or closed on its own. T10 P7 records
    # them in the run service, where every entry (the Run button, a batch,
    # a trigger key, a schedule, an agent's tool) passes.
    "a scheduled run starting; a run started by an agent's run_workflow tool": Gap(
        "T10 P7", "workflow.run_started"
    ),
    "a run refused at admission, or a waiting run refused when it is continued": Gap(
        "T10 P7", "workflow.run_refused"
    ),
    "a run closed because its server process stopped (stale heartbeat)": Gap(
        "T10 P7", "workflow.run_closed"
    ),
    "a trigger call with a valid key whose workflow owner is switched off": Gap(
        "T10 P7", "workflow.run_refused"
    ),
    "a request closed because its run was deleted, cancelled or failed": Gap(
        "T10 P7", "workflow.run_cancelled / workflow.run_closed"
    ),
}

# Records written outside any route: action -> the test that shows it
# (``tests/<file>.py::<Class>::<test>``).
NON_ROUTE_ACTIONS: dict[str, str] = {
    # The daily retention sweep, a background task.
    "audit.retention_sweep": (
        "tests/test_audit_retention.py::TestASweep::"
        "test_it_leaves_one_record_with_what_went_from_each_table"
    ),
    # In the code of the email-change route, which answers 501 today (EXEMPT,
    # ``disabled``); the test holds that it does nothing and records nothing.
    "auth.email_change": (
        "tests/test_audit_account.py::TestEmailChange::"
        "test_the_endpoint_is_switched_off_and_records_nothing"
    ),
    # Written by the confirmation service (a forbidden decision) and by the
    # channel callback (a card click from an unlinked or disabled account).
    "confirmation.decision_denied": (
        "tests/test_audit_confirmations.py::TestSomeoneWhoMayNotDecide::"
        "test_a_forbidden_decision_in_the_portal_is_recorded_once"
    ),
    # The summary of failed sign-ins for unknown accounts that were only counted.
    "auth.login_unknown_account": (
        "tests/test_audit_sign_in.py::TestAnAccountThatDoesNotExist::"
        "test_rows_for_unknown_accounts_from_one_address_are_bounded"
    ),
    # The expiry sweep, a wait that runs out, and a click that comes too late.
    "confirmation.expire": (
        "tests/test_audit_confirmations.py::TestNobodyDecidedInTime::"
        "test_the_sweep_records_each_request_it_closes"
    ),
    # The IP rule gate is a middleware; it refuses before any route.
    "security.ip_blocked": (
        "tests/test_audit_refusals.py::TestABlockedAddress::"
        "test_ip_block_recorded_once_per_window"
    ),
    # Written where the trigger routes look the key up. The routes themselves
    # are listed under KNOWN_GAPS for the run they start (T10 P7).
    "workflow.trigger_key_invalid": (
        "tests/test_audit_refusals.py::TestAnUnknownTriggerKey::"
        "test_recorded_once_per_address_with_only_the_start_of_the_key"
    ),
    # Only the connector builder's test tool sends this request; no route does.
    "connector.action_test": (
        "tests/test_audit_builder_tools.py::TestConnectorBuilder::"
        "test_a_test_call_is_recorded_without_its_arguments"
    ),
    # Also written by the payment processor's webhook (a route, see AUDITED);
    # here: the sweep that ends a cancelled subscription at its period end.
    "billing.plan_change": (
        "tests/test_audit_plan_changes.py::TestThePeriodEndSweep::"
        "test_a_cancelled_subscription_past_its_period_puts_the_user_back_on_free"
    ),
}

# Where a route's record is written when not in the module of its endpoint
# (a shared service): route -> the modules that hold the call.
RECORDED_IN: dict[Route, tuple[str, ...]] = {
    # Every surface decides through the one service, which writes the record.
    ("POST", "/api/confirmations/{confirmation_id}/respond"): (
        "fim_one.core.assembly.confirmation",
    ),
    ("POST", "/api/channels/{channel_id}/callback"): ("fim_one.core.assembly.confirmation",),
    # The schema form's saves and the database builder tools share the recorder.
    ("PUT", "/api/connectors/{connector_id}/schemas/bulk"): ("fim_one.core.audit.resources",),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}"): (
        "fim_one.core.audit.resources",
    ),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}/columns/{col_id}"): (
        "fim_one.core.audit.resources",
    ),
}


# ---------------------------------------------------------------------------
# Writers that are not routes
# ---------------------------------------------------------------------------

# Builtin tools (``core/tool/builtin``) that commit to the database: an agent
# calls them in a chat, so no route sees the change. ``test_coverage.py``
# finds every tool class that commits and requires it here, in one table or
# the other. Class name -> (the actions it records, the test that shows it).
_TOOLS = "tests/test_audit_builder_tools.py::"
TOOL_WRITERS: dict[str, tuple[tuple[str, ...], str]] = {
    "ConnectorCreateActionTool": (
        ("connector.action_create",),
        _TOOLS + "TestConnectorBuilder::test_creating_changing_and_deleting_an_action",
    ),
    "ConnectorUpdateActionTool": (
        ("connector.action_update",),
        _TOOLS + "TestConnectorBuilder::test_creating_changing_and_deleting_an_action",
    ),
    "ConnectorDeleteActionTool": (
        ("connector.action_delete",),
        _TOOLS + "TestConnectorBuilder::test_creating_changing_and_deleting_an_action",
    ),
    "ConnectorTestActionTool": (
        ("connector.action_test",),
        _TOOLS + "TestConnectorBuilder::test_a_test_call_is_recorded_without_its_arguments",
    ),
    "ConnectorUpdateSettingsTool": (
        ("connector.update",),
        _TOOLS + "TestConnectorBuilder::test_changing_where_the_connector_calls_and_how_it_signs_in",
    ),
    "ConnectorImportOpenAPITool": (
        ("connector.actions_import",),
        _TOOLS + "TestConnectorBuilder::test_importing_actions_from_an_openapi_document",
    ),
    "AgentUpdateSettingsTool": (
        ("agent.update",),
        _TOOLS + "TestAgentBuilder::test_tool_categories_and_bound_connectors",
    ),
    "AgentAddConnectorTool": (
        ("agent.update",),
        _TOOLS + "TestAgentBuilder::test_tool_categories_and_bound_connectors",
    ),
    "AgentRemoveConnectorTool": (
        ("agent.update",),
        _TOOLS + "TestAgentBuilder::test_tool_categories_and_bound_connectors",
    ),
    "AgentSetModelTool": (
        ("agent.update",),
        _TOOLS + "TestAgentBuilder::test_a_model_change_is_content_and_leaves_no_record",
    ),
    "DbRunSampleQueryTool": (
        ("database.query",),
        _TOOLS + "TestDatabaseBuilder::test_a_sample_query_is_recorded_like_the_portal_query_box",
    ),
    "DbUpdateConnectorSettingsTool": (
        ("connector.update",),
        _TOOLS + "TestDatabaseBuilder::test_switching_read_only_off",
    ),
    "DbSetTableVisibilityTool": (
        ("connector.schema_fence_update",),
        _TOOLS + "TestDatabaseBuilder::test_hiding_and_showing_tables",
    ),
    "DbBatchSetVisibilityTool": (
        ("connector.schema_fence_update",),
        _TOOLS + "TestDatabaseBuilder::test_hiding_and_showing_tables",
    ),
}
# Tools that commit and record nothing, with the reason.
TOOL_WRITERS_EXEMPT: dict[str, str] = {
    "DbAnnotateTableTool": "sets a table's display name and description: an annotation",
    "DbAnnotateColumnTool": "sets a column's display name and description: an annotation",
    "AskUserQuestionTool": (
        "stores the user's answer to the agent's question; an answer is not a decision"
    ),
}

# Every other module outside ``web/api`` that commits, and what becomes of
# what it writes. A new module that commits must be listed (``test_coverage``).
OTHER_WRITERS: dict[str, str] = {
    "core/audit/writer.py": "the audit writer itself",
    "core/audit/retention.py": "the retention sweep; records audit.retention_sweep",
    "core/assembly/calls.py": "the call record (connector_call_logs), its own table",
    "core/assembly/confirmation.py": (
        "approval requests; decisions and expiries are recorded (confirmation.*)"
    ),
    "core/channels/expiry.py": "the expiry sweep; records confirmation.expire",
    "core/memory/db.py": "conversation messages: content",
    "core/usage/ledger.py": "the usage ledger (usage_ledger), its own table",
    "core/tool/builtin/agent_builder.py": "builder tools, see TOOL_WRITERS",
    "core/tool/builtin/connector_builder.py": "builder tools, see TOOL_WRITERS",
    "core/tool/builtin/db_builder.py": "builder tools, see TOOL_WRITERS",
    "core/tool/builtin/ask_user_question.py": "see TOOL_WRITERS_EXEMPT",
    "core/tool/connector/database/drivers/postgresql.py": (
        "commits on the connector's own database, not this one"
    ),
    "core/workflow/run_service.py": "workflow runs: run events, T10 P7",
    "core/workflow/run_recovery.py": "closes stale runs: run events, T10 P7",
    "core/workflow/run_cleanup.py": (
        "the retention sweep deletes old runs: run events, T10 P7 (the administrators' "
        "manual clean-up is recorded: workflow.cleanup_runs)"
    ),
    "core/workflow/scheduler.py": "starts scheduled runs: run events, T10 P7",
    "web/audit_events.py": "helpers that record",
    "web/auth.py": "an API key's last-used time and request count: a counter, by design",
    "web/services/billing_flag.py": (
        "the billing switch, called from the administration routes that record "
        "billing.activate / enable / disable"
    ),
    "web/services/subscription_lifecycle.py": "records billing.plan_change",
}
# Command-line scripts for developers that write to the database with no
# user and no request (``scripts/``). Not recorded: they are run by whoever
# operates the instance, on its database directly.
UNRECORDED_SCRIPTS = ("scripts/seed_workflow_examples.py", "scripts/smoke_feishu_gate.py")


# ---------------------------------------------------------------------------
# Behaviour proof
# ---------------------------------------------------------------------------

# The tests that drive the application over HTTP and whose requests are
# observed: for every AUDITED and AUDITED_READS route, one of them must
# reach the route with a request that succeeds and writes a row of each
# listed action (``test_route_proofs.py``).
PROOF_TESTS = (
    "tests/test_audit_account.py",
    "tests/test_audit_admin.py",
    "tests/test_audit_admin_transaction.py",
    "tests/test_audit_builder_tools.py",
    "tests/test_audit_channels_credentials.py",
    "tests/test_audit_confirmations.py",
    "tests/test_audit_exports.py",
    "tests/test_audit_organizations.py",
    "tests/test_audit_plan_changes.py",
    "tests/test_audit_redact.py",
    "tests/test_audit_refusals.py",
    "tests/test_audit_request_meta.py",
    "tests/test_audit_resources.py",
    "tests/test_audit_sign_in.py",
    "tests/test_audit_trigger_keys_schedules.py",
    "tests/test_csv_safe.py",
    "tests/audit/test_route_smoke.py",
)
# The plain smoke requests, as opposed to the tests written for a group of
# actions; only used to say how each route is proven.
SMOKE_TESTS = ("tests/audit/test_route_smoke.py",)

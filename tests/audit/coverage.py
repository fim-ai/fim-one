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


# Work outside this branch that a gap may be left to. Nothing else may own
# a gap once the coverage work is done.
DEFERRED_OWNERS = frozenset({"T10 P7"})


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
    ("DELETE", "/api/admin/market/resources/{resource_type}/{resource_id}"): ("market.resource_delete",),
    ("PATCH", "/api/admin/market/resources/{resource_type}/{resource_id}/unpublish"): ("market.resource_unpublish",),
    ("POST", "/api/admin/model-config/import"): ("model_config.import",),
    ("POST", "/api/admin/model-groups"): ("model_group.create",),
    ("POST", "/api/admin/model-groups/deactivate"): ("model_group.deactivate_all",),
    ("DELETE", "/api/admin/model-groups/{group_id}"): ("model_group.delete",),
    ("PUT", "/api/admin/model-groups/{group_id}"): ("model_group.update",),
    ("PATCH", "/api/admin/model-groups/{group_id}/activate"): ("model_group.activate",),
    ("POST", "/api/admin/model-provider-models/batch-delete"): ("model_provider_model.batch_delete",),
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
    ("POST", "/api/admin/reviews/batch-approve"): ("review.admin_batch_approve", "review_log:approved"),
    ("POST", "/api/admin/reviews/batch-reject"): ("review.admin_batch_reject", "review_log:rejected"),
    ("PATCH", "/api/admin/schedules/{workflow_id}/active"): ("schedule.admin_pause", "schedule.admin_resume"),
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
    ("POST", "/api/agents/{agent_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/agents/{agent_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/agents/{agent_id}/unpublish"): ("review_log:unpublished",),
    ("POST", "/api/auth/2fa/backup-codes"): ("auth.backup_codes_regenerate",),
    ("POST", "/api/auth/2fa/disable"): ("auth.two_factor_disable",),
    ("POST", "/api/auth/2fa/enable"): ("auth.two_factor_enable",),
    ("DELETE", "/api/auth/account"): ("account.self_delete",),
    ("POST", "/api/auth/change-email/confirm"): ("auth.email_change",),
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
    ("POST", "/api/connectors/{connector_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/connectors/{connector_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/connectors/{connector_id}/unpublish"): ("review_log:unpublished",),
    ("POST", "/api/mcp-servers/{server_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/mcp-servers/{server_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/mcp-servers/{server_id}/unpublish"): ("review_log:unpublished",),
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
    ("POST", "/api/skills/{skill_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/skills/{skill_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/skills/{skill_id}/unpublish"): ("review_log:unpublished",),
    ("POST", "/api/workflows/{workflow_id}/publish"): ("review_log:submitted",),
    ("POST", "/api/workflows/{workflow_id}/resubmit"): ("review_log:resubmitted",),
    ("POST", "/api/workflows/{workflow_id}/unpublish"): ("review_log:unpublished",),
}

EXEMPT: dict[Route, str] = {
    # content
    ("POST", "/api/agents/{agent_id}/ai/refine"): CONTENT,
    ("DELETE", "/api/auth/avatar"): CONTENT,
    ("POST", "/api/auth/avatar"): CONTENT,
    ("PATCH", "/api/auth/profile"): CONTENT,
    ("POST", "/api/confirmations/{confirmation_id}/answer"): CONTENT,
    ("POST", "/api/connectors/{connector_id}/ai/generate-actions"): CONTENT,
    ("POST", "/api/connectors/{connector_id}/ai/refine-action"): CONTENT,
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
    ("PUT", "/api/knowledge-bases/{kb_id}"): CONTENT,
    ("DELETE", "/api/knowledge-bases/{kb_id}/chunks/{chunk_id}"): CONTENT,
    ("PUT", "/api/knowledge-bases/{kb_id}/chunks/{chunk_id}"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents/create"): CONTENT,
    ("DELETE", "/api/knowledge-bases/{kb_id}/documents/{doc_id}"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/documents/{doc_id}/retry"): CONTENT,
    ("POST", "/api/knowledge-bases/{kb_id}/import-urls"): CONTENT,
    ("PUT", "/api/me/notifications"): CONTENT,
    ("PUT", "/api/skills/{skill_id}"): CONTENT,
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
    # probe
    ("POST", "/api/admin/sensitive-words/check"): PROBE,
    ("POST", "/api/auth/2fa/setup"): PROBE,
    ("POST", "/api/auth/verify-forgot-code"): PROBE,
    ("POST", "/api/channels/discover-chats"): PROBE,
    ("POST", "/api/channels/{channel_id}/test-approval"): PROBE,
    ("POST", "/api/connectors/test-connection"): PROBE,
    ("POST", "/api/connectors/{connector_id}/circuit-breaker-reset"): PROBE,
    ("POST", "/api/connectors/{connector_id}/introspect"): PROBE,
    ("POST", "/api/connectors/{connector_id}/test-connection"): PROBE,
    ("POST", "/api/knowledge-bases/{kb_id}/retrieve"): PROBE,
    ("POST", "/api/mcp-servers/{server_id}/test"): PROBE,
    ("POST", "/api/workflows/validate"): PROBE,
    ("POST", "/api/workflows/{workflow_id}/test-webhook"): PROBE,
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
    ("POST", "/api/builder/session"): SESSION,
    ("POST", "/api/chat/ticket"): SESSION,
    # payment_redirect
    ("POST", "/api/billing/checkout"): PAYMENT_REDIRECT,
    ("POST", "/api/billing/portal"): PAYMENT_REDIRECT,
}

KNOWN_GAPS: dict[Route, Gap] = {
    # 4 channels and credentials
    ("POST", "/api/channels"): Gap("4 channels and credentials", "channel.create"),
    ("DELETE", "/api/channels/{channel_id}"): Gap("4 channels and credentials", "channel.delete"),
    ("PATCH", "/api/channels/{channel_id}"): Gap("4 channels and credentials", "channel.update"),
    ("POST", "/api/channels/{channel_id}/test"): Gap("4 channels and credentials", "channel.test"),
    ("DELETE", "/api/connectors/{connector_id}/my-credentials"): Gap("4 channels and credentials", "connector.credential_delete"),
    ("PUT", "/api/connectors/{connector_id}/my-credentials"): Gap("4 channels and credentials", "connector.credential_set"),
    ("PUT", "/api/mcp-servers/{server_id}/my-credentials"): Gap("4 channels and credentials", "mcp_server.credential_set"),
    ("PUT", "/api/workflows/{workflow_id}/env"): Gap("4 channels and credentials", "workflow.env_update"),
    # 5 trigger keys and schedules
    ("DELETE", "/api/workflows/{workflow_id}/api-key"): Gap("5 trigger keys and schedules", "workflow.trigger_key_revoke"),
    ("POST", "/api/workflows/{workflow_id}/generate-api-key"): Gap("5 trigger keys and schedules", "workflow.trigger_key_generate"),
    ("DELETE", "/api/workflows/{workflow_id}/schedule"): Gap("5 trigger keys and schedules", "workflow.schedule_delete"),
    ("PUT", "/api/workflows/{workflow_id}/schedule"): Gap("5 trigger keys and schedules", "workflow.schedule_update"),
    # 6 resource lifecycle
    ("POST", "/api/agent-templates/{template_id}/create"): Gap("6 resource lifecycle", "agent.create"),
    ("POST", "/api/agents"): Gap("6 resource lifecycle", "agent.create"),
    ("POST", "/api/agents/ai/create"): Gap("6 resource lifecycle", "agent.create"),
    ("DELETE", "/api/agents/{agent_id}"): Gap("6 resource lifecycle", "agent.delete"),
    ("PUT", "/api/agents/{agent_id}"): Gap("6 resource lifecycle", "agent.update"),
    ("POST", "/api/agents/{agent_id}/fork"): Gap("6 resource lifecycle", "agent.create"),
    ("POST", "/api/connector-templates/{template_id}/create"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors/ai/create"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors/from-config"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors/import"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors/import-openapi"): Gap("6 resource lifecycle", "connector.create"),
    ("DELETE", "/api/connectors/{connector_id}"): Gap("6 resource lifecycle", "connector.delete"),
    ("PUT", "/api/connectors/{connector_id}"): Gap("6 resource lifecycle", "connector.update"),
    ("POST", "/api/connectors/{connector_id}/actions"): Gap("6 resource lifecycle", "connector.action_create"),
    ("DELETE", "/api/connectors/{connector_id}/actions/{action_id}"): Gap("6 resource lifecycle", "connector.action_delete"),
    ("PUT", "/api/connectors/{connector_id}/actions/{action_id}"): Gap("6 resource lifecycle", "connector.action_update"),
    ("POST", "/api/connectors/{connector_id}/fork"): Gap("6 resource lifecycle", "connector.create"),
    ("POST", "/api/connectors/{connector_id}/import-openapi"): Gap("6 resource lifecycle", "connector.actions_import"),
    ("PUT", "/api/connectors/{connector_id}/schemas/bulk"): Gap("6 resource lifecycle", "connector.schema_fence_update"),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}"): Gap("6 resource lifecycle", "connector.schema_fence_update"),
    ("PUT", "/api/connectors/{connector_id}/schemas/{schema_id}/columns/{col_id}"): Gap("6 resource lifecycle", "connector.schema_fence_update"),
    ("POST", "/api/connectors/{connector_id}/toggle"): Gap("6 resource lifecycle", "connector.update"),
    ("POST", "/api/knowledge-bases"): Gap("6 resource lifecycle", "kb.create"),
    ("DELETE", "/api/knowledge-bases/{kb_id}"): Gap("6 resource lifecycle", "kb.delete"),
    ("POST", "/api/knowledge-bases/{kb_id}/toggle"): Gap("6 resource lifecycle", "kb.update"),
    ("POST", "/api/market/subscribe"): Gap("6 resource lifecycle", "market.subscribe"),
    ("DELETE", "/api/market/unsubscribe"): Gap("6 resource lifecycle", "market.unsubscribe"),
    ("POST", "/api/mcp-servers"): Gap("6 resource lifecycle", "mcp_server.create"),
    ("DELETE", "/api/mcp-servers/{server_id}"): Gap("6 resource lifecycle", "mcp_server.delete"),
    ("PUT", "/api/mcp-servers/{server_id}"): Gap("6 resource lifecycle", "mcp_server.update"),
    ("POST", "/api/mcp-servers/{server_id}/fork"): Gap("6 resource lifecycle", "mcp_server.create"),
    ("POST", "/api/skill-templates/{template_id}/create"): Gap("6 resource lifecycle", "skill.create"),
    ("POST", "/api/skills"): Gap("6 resource lifecycle", "skill.create"),
    ("DELETE", "/api/skills/{skill_id}"): Gap("6 resource lifecycle", "skill.delete"),
    ("POST", "/api/skills/{skill_id}/fork"): Gap("6 resource lifecycle", "skill.create"),
    ("POST", "/api/skills/{skill_id}/toggle"): Gap("6 resource lifecycle", "skill.update"),
    ("POST", "/api/workflow-templates/{template_id}/create-workflow"): Gap("6 resource lifecycle", "workflow.create"),
    ("POST", "/api/workflows"): Gap("6 resource lifecycle", "workflow.create"),
    ("POST", "/api/workflows/from-template"): Gap("6 resource lifecycle", "workflow.create"),
    ("POST", "/api/workflows/import"): Gap("6 resource lifecycle", "workflow.create"),
    ("DELETE", "/api/workflows/{workflow_id}"): Gap("6 resource lifecycle", "workflow.delete"),
    ("PUT", "/api/workflows/{workflow_id}"): Gap("6 resource lifecycle", "workflow.update"),
    ("POST", "/api/workflows/{workflow_id}/fork"): Gap("6 resource lifecycle", "workflow.create"),
    ("DELETE", "/api/workflows/{workflow_id}/runs"): Gap("6 resource lifecycle", "workflow.runs_clear"),
    ("DELETE", "/api/workflows/{workflow_id}/runs/{run_id}"): Gap("6 resource lifecycle", "workflow.run_delete"),
    # 7 plan changes
    ("POST", "/api/webhooks/stripe"): Gap("7 plan changes", "billing.plan_change"),
    # 8 exports and views
    ("POST", "/api/connectors/{connector_id}/query"): Gap("8 exports and views", "database.query"),
    # 10 admin leftovers
    ("PATCH", "/api/admin/agents/{agent_id}/active"): Gap("10 admin leftovers", "agent.admin_enable|agent.admin_disable"),
    ("POST", "/api/admin/market/import-templates"): Gap("10 admin leftovers", "market.import_templates"),
    ("DELETE", "/api/admin/organizations/{org_id}"): Gap("10 admin leftovers", "org.admin_delete"),
    ("PUT", "/api/admin/organizations/{org_id}"): Gap("10 admin leftovers", "org.admin_update"),
    ("PATCH", "/api/admin/users/{user_id}"): Gap("10 admin leftovers", "user.update"),
    ("POST", "/api/notifications/send"): Gap("10 admin leftovers", "notifications.send"),
    ("POST", "/api/notifications/test"): Gap("10 admin leftovers", "notifications.test"),
    # 11 confirmation decisions
    ("POST", "/api/channels/{channel_id}/callback"): Gap("11 confirmation decisions", "confirmation.approve|confirmation.reject"),
    ("POST", "/api/confirmations/{confirmation_id}/respond"): Gap("11 confirmation decisions", "confirmation.approve|confirmation.reject"),
    # T10 P7
    ("POST", "/api/workflows/trigger"): Gap("T10 P7", "workflow.run_started / workflow.run_refused"),
    ("POST", "/api/workflows/trigger/{api_key}"): Gap("T10 P7", "workflow.run_started / workflow.run_refused"),
    ("POST", "/api/workflows/{workflow_id}/batch-run"): Gap("T10 P7", "workflow.run_started / workflow.run_refused"),
    ("POST", "/api/workflows/{workflow_id}/run"): Gap("T10 P7", "workflow.run_started / workflow.run_refused"),
    ("POST", "/api/workflows/{workflow_id}/runs/{run_id}/cancel"): Gap("T10 P7", "workflow.run_cancelled"),
    ("POST", "/api/workflows/{workflow_id}/test-node"): Gap("T10 P7", "workflow.run_started / workflow.run_refused"),
}


# GET routes that leave a record: an export, a look at someone else's data.
AUDITED_READS: dict[Route, tuple[str, ...]] = {
    ("GET", "/api/admin/conversations/{conv_id}/messages"): ("conversation.viewed",),
    ("GET", "/api/admin/export/conversations"): ("export.conversations",),
    ("GET", "/api/admin/export/full-backup"): ("export.full_backup",),
    ("GET", "/api/admin/export/users"): ("export.users",),
    ("GET", "/api/admin/model-config/export"): ("model_config.export",),
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
    "export of one's own data, a conversation, a workflow's runs": Gap(
        "8 exports and views", "export.own_data / export.conversation / export.workflow_runs"
    ),
    "an administrator exporting the audit log or reading the system logs": Gap(
        "8 exports and views", "export.audit_log / system_logs.read"
    ),
    "a plan ending at its period end (lifecycle sweep)": Gap(
        "7 plan changes", "billing.plan_change"
    ),
    "a request refused by the IP rules": Gap("9 refusals", "security.ip_blocked"),
    "a trigger call with an unknown key": Gap("9 refusals", "workflow.trigger_key_invalid"),
    "a request nobody decided in time": Gap("11 confirmation decisions", "confirmation.expire"),
    "someone other than the owner unpublishing a resource": Gap(
        "6 resource lifecycle", "<kind>.unpublish_by_admin"
    ),
    "a scheduled run starting, a waiting run refused on resume, a stale run closed": Gap(
        "T10 P7", "workflow.run_started / workflow.run_refused / workflow.run_closed"
    ),
}

# Records written outside any route: action -> the test that shows it
# (``tests/<file>.py::<Class>::<test>``).
NON_ROUTE_ACTIONS: dict[str, str] = {}

# Where a route's record is written when not in the module of its endpoint
# (a shared service): route -> the modules that hold the call.
RECORDED_IN: dict[Route, tuple[str, ...]] = {}

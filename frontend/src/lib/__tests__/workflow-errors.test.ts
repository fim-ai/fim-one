import { describe, it, expect, vi, beforeEach } from "vitest"
import { createTranslator } from "next-intl"

const toastError = vi.fn()
vi.mock("sonner", () => ({ toast: { error: (...args: unknown[]) => toastError(...args) } }))

import { ApiError } from "@/lib/api"
import { getErrorMessage, translateErrorCode, type TranslatorFn } from "@/lib/error-utils"
import {
  blueprintFieldErrors,
  describeWorkflowError,
  fieldOfLoc,
  formatBlueprintFieldErrors,
  toastWorkflowError,
} from "@/lib/workflow-errors"
import errorsEn from "../../../messages/en/errors.json"
import workflowsEn from "../../../messages/en/workflows.json"

/** The real English messages, through a real next-intl translator. */
function translator(namespace: "errors" | "workflows"): TranslatorFn {
  return createTranslator({
    locale: "en",
    messages: { errors: errorsEn, workflows: workflowsEn },
    namespace,
    onError: () => {},
  }) as unknown as TranslatorFn
}
const tError = translator("errors")
const tWorkflows = translator("workflows")

const schemaError = (errors: unknown) =>
  new ApiError(422, "The workflow blueprint has invalid fields", "blueprint_schema_invalid", {
    errors,
  })

const FIELD_ERRORS = [
  { loc: "nodes.3.data.timeout_hours", node_id: "h_1", message: "Input should be greater than 0" },
  { loc: "nodes.1.data.top_k", node_id: "kb_1", message: "Input should be a valid integer" },
]

describe("getErrorMessage", () => {
  it("uses the message of a code that has one", () => {
    const err = new ApiError(429, "server text", "workflow_already_running")
    expect(getErrorMessage(err, tError)).toBe(
      "This workflow is already running. Wait for the run to finish.",
    )
  })

  it("falls back to the server's text for a code without a message", () => {
    // next-intl answers a missing key with "errors.<key>", which used to be shown.
    const err = new ApiError(400, "Some new refusal", "code_nobody_translated")
    expect(getErrorMessage(err, tError)).toBe("Some new refusal")
  })

  it("falls back to the server's text for a code that carries free text", () => {
    const err = new ApiError(400, "Invalid blueprint: node x.y", "invalid_blueprint: node x.y")
    expect(getErrorMessage(err, tError)).toBe("Invalid blueprint: node x.y")
  })

  it("treats a message whose arguments are missing as no message", () => {
    expect(translateErrorCode("ip_rule_self_lockout", tError)).toBeNull()
    expect(translateErrorCode("ip_rule_self_lockout", tError, { ip: "10.0.0.1" })).toContain(
      "10.0.0.1",
    )
  })
})

describe("workflow error codes", () => {
  it.each([
    "blueprint_schema_invalid",
    "blueprint_empty",
    "human_intervention_disabled",
    "workflow_not_found",
    "workflow_run_not_found",
    "workflow_version_not_found",
    "workflow_versions_owner_only",
    "import_invalid_format",
    "import_invalid_blueprint",
    "workflow_inactive",
    "workflow_owner_inactive",
    "run_identity_missing",
    "workflow_has_approval_step",
    "workflow_rate_limited",
    "workflow_already_running",
    "cannot_delete_active_run",
    "node_not_found",
    "node_not_testable",
    "schedule_cron_required",
    "invalid_webhook_url",
    "webhook_url_not_configured",
    "no_api_key",
    "invalid_api_key",
    "approval_not_found",
    "approval_not_assigned",
    "approval_already_resolved",
    "template_not_found",
    "fork_denied",
    "export_denied",
    "unpublish_denied",
    "org_id_required",
    "invalid_scope",
    "not_rejected",
    "account_disabled",
    "token_quota_exceeded",
    "subscription_required",
  ])("%s has an English message that needs no arguments", (code) => {
    const message = translateErrorCode(code, tError)
    expect(message).not.toBeNull()
    expect(message).not.toContain("{")
  })
})

describe("run input and approval refusals", () => {
  it("names the input a run was refused for", () => {
    const missing = new ApiError(422, "Input 'count' is required.", "workflow_input_required", {
      input: "count",
    })
    expect(getErrorMessage(missing, tError)).toBe('Input "count" is required.')
    const unfit = new ApiError(422, "Input 'count' must be a number.", "workflow_input_invalid", {
      input: "count",
      expected: "a number",
    })
    expect(getErrorMessage(unfit, tError)).toBe('Input "count" must be a number.')
  })

  it.each(["confirmation_already_decided", "confirmation_forbidden", "confirmation_not_found"])(
    "%s has an English message",
    (code) => {
      expect(translateErrorCode(code, tError, { status: "approved" })).not.toBeNull()
    },
  )
})

describe("fieldOfLoc", () => {
  it.each([
    ["nodes.3.data.timeout_hours", "timeout_hours"],
    ["nodes.0.data.variables.2.name", "variables.2.name"],
    ["nodes.1.type", "type"],
    ["edges.0.source", "edges.0.source"],
    ["viewport.zoom", "viewport.zoom"],
  ])("%s -> %s", (loc, field) => {
    expect(fieldOfLoc(loc)).toBe(field)
  })
})

describe("blueprintFieldErrors", () => {
  it("keeps well-formed entries and drops the rest", () => {
    expect(
      blueprintFieldErrors({
        errors: [FIELD_ERRORS[0], null, "x", { loc: 1, message: "m" }, { loc: "edges.0.id", message: "m" }],
      }),
    ).toEqual([FIELD_ERRORS[0], { loc: "edges.0.id", node_id: null, message: "m" }])
  })

  it("is empty when the response carries no list", () => {
    expect(blueprintFieldErrors({})).toEqual([])
    expect(blueprintFieldErrors({ errors: "nope" })).toEqual([])
  })
})

describe("formatBlueprintFieldErrors", () => {
  it("names the step by its canvas label, the field and the reason", () => {
    const labels: Record<string, string> = { h_1: "Manager sign-off" }
    expect(formatBlueprintFieldErrors(FIELD_ERRORS, tWorkflows, (id) => labels[id])).toEqual([
      "Manager sign-off, timeout_hours: Input should be greater than 0",
      // No label on the canvas: the step id stands in.
      "kb_1, top_k: Input should be a valid integer",
    ])
  })

  it("shows a field outside any step by its path", () => {
    expect(
      formatBlueprintFieldErrors(
        [{ loc: "edges.0.source", node_id: null, message: "Field required" }],
        tWorkflows,
      ),
    ).toEqual(["edges.0.source: Field required"])
  })

  it("lists three fields and counts the rest", () => {
    const many = Array.from({ length: 5 }, (_, i) => ({
      loc: `nodes.${i}.data.model`,
      node_id: `llm_${i}`,
      message: "Input should be a valid string",
    }))
    const lines = formatBlueprintFieldErrors(many, tWorkflows)
    expect(lines).toHaveLength(4)
    expect(lines[3]).toBe("and 2 more fields")
    expect(formatBlueprintFieldErrors(many.slice(0, 4), tWorkflows)[3]).toBe("and 1 more field")
  })
})

describe("describeWorkflowError", () => {
  it("describes an invalid blueprint by step and field", () => {
    expect(
      describeWorkflowError(schemaError(FIELD_ERRORS), "Failed to save workflow", tError, tWorkflows),
    ).toEqual({
      title: "The workflow has invalid settings and was not saved.",
      lines: [
        "h_1, timeout_hours: Input should be greater than 0",
        "kb_1, top_k: Input should be a valid integer",
      ],
      code: "blueprint_schema_invalid",
    })
  })

  it("lists the switched-off steps to remove", () => {
    const err = new ApiError(422, "server text", "human_intervention_disabled", {
      nodes: "Sign off, Legal review",
      errors: [],
    })
    expect(describeWorkflowError(err, "Failed to save workflow", tError, tWorkflows)).toEqual({
      title:
        "Human Review steps are switched off on this instance. Remove them from the workflow to continue.",
      lines: ["Steps to remove: Sign off, Legal review"],
      code: "human_intervention_disabled",
    })
  })

  it("uses the code's message for any other refusal", () => {
    const err = new ApiError(429, "Too many concurrent workflow runs.", "workflow_rate_limited")
    expect(describeWorkflowError(err, "Failed to run batch", tError, tWorkflows)).toEqual({
      title: "Too many workflow runs right now. Wait for a run to finish, or try again in a minute.",
      lines: [],
      code: "workflow_rate_limited",
    })
  })

  it("uses the fallback for a failure without a code, and nothing without a fallback", () => {
    const network = new TypeError("Failed to fetch")
    expect(describeWorkflowError(network, "Failed to save workflow", tError, tWorkflows)).toEqual({
      title: "Failed to save workflow",
      lines: [],
      code: null,
    })
    expect(describeWorkflowError(network, null, tError, tWorkflows)).toBeNull()
    expect(
      describeWorkflowError(new ApiError(500, "boom"), null, tError, tWorkflows),
    ).toBeNull()
  })
})

describe("toastWorkflowError", () => {
  beforeEach(() => {
    toastError.mockReset()
  })

  it("shows a refused save even when there is no fallback (auto-save)", () => {
    expect(toastWorkflowError(schemaError(FIELD_ERRORS), null, tError, tWorkflows)).toBe(true)
    expect(toastError).toHaveBeenCalledTimes(1)
    const [title, options] = toastError.mock.calls[0]
    expect(title).toBe("The workflow has invalid settings and was not saved.")
    // One id per code, so auto-save retries replace the toast instead of stacking.
    expect(options.id).toBe("blueprint_schema_invalid")
    expect(options.description).toBe(
      "h_1, timeout_hours: Input should be greater than 0\nkb_1, top_k: Input should be a valid integer",
    )
  })

  it("stays silent for an auto-save that failed without a code", () => {
    expect(toastWorkflowError(new TypeError("Failed to fetch"), null, tError, tWorkflows)).toBe(false)
    expect(toastError).not.toHaveBeenCalled()
  })

  it("shows the fallback for a manual save that failed without a code", () => {
    toastWorkflowError(new TypeError("Failed to fetch"), "Failed to save workflow", tError, tWorkflows)
    expect(toastError).toHaveBeenCalledWith("Failed to save workflow")
  })
})

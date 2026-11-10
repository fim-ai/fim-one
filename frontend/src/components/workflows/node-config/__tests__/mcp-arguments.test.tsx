import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"
import type { ReactNode } from "react"

import workflows from "../../../../../messages/en/workflows.json"
import errors from "../../../../../messages/en/errors.json"
import { defaultNodeData } from "@/lib/workflow-blueprint"
import { MCPForm } from "../mcp-form"
import { forgetMcpTools, MCP_TOOLS_TTL_MS } from "../mcp-tools-memory"
import {
  argumentFields,
  fromArgumentText,
  missingArguments,
  parseArgumentsJson,
  toArgumentText,
  toArgumentsJson,
  withArgument,
} from "../mcp-arguments"

/**
 * The MCP step had no way to enter a tool's arguments: a tool that needs
 * any could not be used from the editor. The form now lists the server's
 * tools and shows a field for each argument of the chosen tool's schema,
 * with a JSON editor when the schema is not available.
 */

const SEARCH_SCHEMA = {
  type: "object",
  properties: {
    limit: { type: "integer", description: "How many results" },
    query: { type: "string", description: "What to look for" },
    mode: { type: "string", enum: ["fast", "deep"] },
    exact: { type: "boolean" },
    filter: { type: "object" },
  },
  required: ["query"],
}

describe("argumentFields", () => {
  it("gives one field per property, required ones first", () => {
    const fields = argumentFields(SEARCH_SCHEMA)
    expect(fields?.map((f) => f.name)).toEqual(["query", "limit", "mode", "exact", "filter"])
    expect(fields?.[0]).toEqual({
      name: "query",
      type: "string",
      description: "What to look for",
      required: true,
      options: null,
    })
    expect(fields?.find((f) => f.name === "mode")?.options).toEqual(["fast", "deep"])
  })

  it("reads a nullable type and an undeclared one", () => {
    const fields = argumentFields({
      type: "object",
      properties: { n: { type: ["integer", "null"] }, anything: {} },
    })
    expect(fields?.map((f) => f.type)).toEqual(["integer", "any"])
  })

  it("is empty for a tool that takes no arguments", () => {
    expect(argumentFields({ type: "object", properties: {} })).toEqual([])
    expect(argumentFields({ type: "object" })).toEqual([])
  })

  it("is null when the schema names no properties to show", () => {
    expect(argumentFields(null)).toBeNull()
    expect(argumentFields("object")).toBeNull()
    expect(argumentFields({ type: "object", additionalProperties: true })).toBeNull()
    expect(argumentFields({ type: "object", properties: ["a"] })).toBeNull()
  })
})

describe("fromArgumentText", () => {
  it("stores text for a string and nothing for an empty field", () => {
    expect(fromArgumentText("hello", "string")).toBe("hello")
    expect(fromArgumentText("42", "string")).toBe("42")
    expect(fromArgumentText("", "integer")).toBeUndefined()
  })

  it("stores numbers as numbers", () => {
    expect(fromArgumentText("42", "integer")).toBe(42)
    expect(fromArgumentText("0.5", "number")).toBe(0.5)
    expect(fromArgumentText("-3", "number")).toBe(-3)
  })

  it("keeps a number that is still being typed as text", () => {
    expect(fromArgumentText("1.", "number")).toBe("1.")
    expect(fromArgumentText("-", "integer")).toBe("-")
    expect(fromArgumentText("1.5", "integer")).toBe("1.5")
    expect(fromArgumentText("abc", "number")).toBe("abc")
  })

  it("stores booleans, objects and arrays as such", () => {
    expect(fromArgumentText("true", "boolean")).toBe(true)
    expect(fromArgumentText("false", "boolean")).toBe(false)
    expect(fromArgumentText('{"a": 1}', "object")).toEqual({ a: 1 })
    expect(fromArgumentText("[1, 2]", "array")).toEqual([1, 2])
    expect(fromArgumentText('{"a": ', "object")).toBe('{"a": ')
    expect(fromArgumentText("[1]", "object")).toBe("[1]")
  })

  it("keeps a variable reference as text whatever the type", () => {
    expect(fromArgumentText("{{start_1.count}}", "integer")).toBe("{{start_1.count}}")
    expect(fromArgumentText("{{start_1.filter}}", "object")).toBe("{{start_1.filter}}")
  })

  it("reads JSON when no type is declared, else text", () => {
    expect(fromArgumentText("7", "any")).toBe(7)
    expect(fromArgumentText("seven", "any")).toBe("seven")
  })

  it("shows a stored value as the text that produces it", () => {
    for (const [text, type] of [
      ["42", "integer"],
      ["true", "boolean"],
      ['{"a":1}', "object"],
      ["hello", "string"],
    ] as const) {
      expect(toArgumentText(fromArgumentText(text, type))).toBe(text)
    }
    expect(toArgumentText(undefined)).toBe("")
  })
})

describe("parameters", () => {
  it("sets and removes one argument without touching the others", () => {
    expect(withArgument({ a: 1 }, "b", "x")).toEqual({ a: 1, b: "x" })
    expect(withArgument({ a: 1, b: "x" }, "b", undefined)).toEqual({ a: 1 })
  })

  it("names the required arguments that are not set", () => {
    const fields = argumentFields(SEARCH_SCHEMA) ?? []
    expect(missingArguments(fields, {})).toEqual(["query"])
    expect(missingArguments(fields, { query: "" })).toEqual(["query"])
    expect(missingArguments(fields, { query: "otters" })).toEqual([])
  })

  it("accepts a JSON object and nothing else in the JSON editor", () => {
    expect(parseArgumentsJson('{"text": "hi"}')).toEqual({ text: "hi" })
    expect(parseArgumentsJson("  ")).toEqual({})
    expect(parseArgumentsJson("[1]")).toBeNull()
    expect(parseArgumentsJson('"text"')).toBeNull()
    expect(parseArgumentsJson('{"text": ')).toBeNull()
    expect(toArgumentsJson({})).toBe("")
    expect(parseArgumentsJson(toArgumentsJson({ a: [1], b: "x" }))).toEqual({ a: [1], b: "x" })
  })
})

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

const backend = vi.hoisted(() => ({
  listTools: null as null | ((serverId: string) => Promise<unknown>),
}))

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    errorCode: string | null
    errorArgs: Record<string, unknown>
    constructor(message: string, errorCode: string | null, errorArgs: Record<string, unknown> = {}) {
      super(message)
      this.errorCode = errorCode
      this.errorArgs = errorArgs
    }
  }
  return {
    ApiError,
    mcpServerApi: {
      list: () =>
        Promise.resolve({ items: [{ id: "s-1", name: "Search", is_active: true }], pages: 1 }),
    },
    workflowApi: { listStepMcpTools: (serverId: string) => backend.listTools?.(serverId) },
  }
})

vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string
    onValueChange: (v: string) => void
    children: ReactNode
  }) => (
    <select value={value} onChange={(e) => onValueChange(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}))

vi.mock("../../variable-picker", () => ({
  VariablePicker: ({ onInsert }: { onInsert: (ref: string) => void }) => (
    <button onClick={() => onInsert("{{start_1.topic}}")}>insert variable</button>
  ),
}))

afterEach(() => {
  cleanup()
  backend.listTools = null
  forgetMcpTools()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

const TOOLS = [
  { name: "search", description: "Search the index.", input_schema: SEARCH_SCHEMA },
  { name: "ping", description: "", input_schema: { type: "object", properties: {} } },
  { name: "raw", description: "", input_schema: null },
]

function renderMcp(data: Record<string, unknown> = {}) {
  const setField = vi.fn()
  const setFields = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }}>
      <MCPForm
        data={{ ...defaultNodeData("mcp"), ...data }}
        setField={setField}
        setFields={setFields}
        otherNodes={[]}
      />
    </NextIntlClientProvider>,
  )
  return { setField, setFields }
}

/** The dropdown that holds an option with this label. */
function selectWith(label: string): HTMLSelectElement {
  const option = screen.getByRole("option", { name: label }) as HTMLOptionElement
  return option.closest("select") as HTMLSelectElement
}

describe("MCP step form", () => {
  it("asks nothing until a server is chosen", () => {
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    renderMcp()
    expect(backend.listTools).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText(workflows.configToolNamePlaceholder)).toBeTruthy()
  })

  it("lists the chosen server's tools and clears the arguments when the tool changes", async () => {
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    const { setFields } = renderMcp({ server_id: "s-1", tool_name: "ping", parameters: {} })
    await screen.findByRole("option", { name: "search" })
    expect(backend.listTools).toHaveBeenCalledWith("s-1")
    expect(Array.from(selectWith("search").options).map((o) => o.textContent)).toEqual([
      workflows.configToolPlaceholder,
      "search",
      "ping",
      "raw",
    ])
    fireEvent.change(selectWith("search"), { target: { value: "search" } })
    expect(setFields).toHaveBeenLastCalledWith({ tool_name: "search", parameters: {} })
  })

  it("shows a field for each argument and stores what is typed in the schema's type", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    const { setField } = renderMcp({
      server_id: "s-1",
      tool_name: "search",
      parameters: { query: "otters" },
    })
    const query = (await screen.findByLabelText(/^query/)) as HTMLInputElement
    expect(query.value).toBe("otters")
    expect(screen.getByText("Search the index.")).toBeTruthy()
    expect(screen.getByText("How many results")).toBeTruthy()

    fireEvent.change(screen.getByLabelText(/^limit/), { target: { value: "5" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { query: "otters", limit: 5 })
    fireEvent.change(selectWith("deep"), { target: { value: "deep" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { query: "otters", mode: "deep" })
    fireEvent.change(selectWith("true"), { target: { value: "true" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { query: "otters", exact: true })
    // Emptying a field removes the argument.
    fireEvent.change(query, { target: { value: "" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", {})
  })

  it("marks a required argument that is not set", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    renderMcp({ server_id: "s-1", tool_name: "search", parameters: {} })
    const query = await screen.findByLabelText(/^query/)
    expect(query.getAttribute("aria-invalid")).toBe("true")
    expect(screen.getByText(workflows.configArgumentRequired)).toBeTruthy()
  })

  it("inserts a picked variable into the field that had focus", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    const { setField } = renderMcp({
      server_id: "s-1",
      tool_name: "search",
      parameters: { query: "about " },
    })
    const limit = await screen.findByLabelText(/^limit/)
    fireEvent.click(screen.getByText("insert variable"))
    expect(setField).toHaveBeenLastCalledWith("parameters", { query: "about {{start_1.topic}}" })
    fireEvent.focus(limit)
    fireEvent.click(screen.getByText("insert variable"))
    expect(setField).toHaveBeenLastCalledWith("parameters", {
      query: "about ",
      limit: "{{start_1.topic}}",
    })
  })

  it("says so when the tool takes no arguments", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    renderMcp({ server_id: "s-1", tool_name: "ping", parameters: {} })
    expect(await screen.findByText(workflows.configArgumentsNone)).toBeTruthy()
  })

  it("falls back to JSON for a tool without a schema, and refuses what is not an object", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    const { setField } = renderMcp({ server_id: "s-1", tool_name: "raw", parameters: {} })
    await screen.findByRole("option", { name: "raw" })
    const json = screen.getByLabelText(workflows.configArgumentsJson)
    // The example is JSON a user could paste, braces and all.
    expect(json.getAttribute("placeholder")).toBe('{ "text": "{{start_1.topic}}" }')
    fireEvent.change(json, { target: { value: '{"text": "hi"}' } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { text: "hi" })
    setField.mockClear()
    fireEvent.change(json, { target: { value: '{"text": ' } })
    expect(setField).not.toHaveBeenCalled()
    expect(json.getAttribute("aria-invalid")).toBe("true")
    expect(
      screen.getByText('Arguments must be a JSON object, for example { "text": "hello" }.'),
    ).toBeTruthy()
    // What was typed stays in the editor.
    expect((json as HTMLTextAreaElement).value).toBe('{"text": ')
  })

  it("can switch a described tool to JSON and back", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    renderMcp({ server_id: "s-1", tool_name: "search", parameters: { query: "otters", limit: 5 } })
    await screen.findByLabelText(/^query/)
    fireEvent.click(screen.getByRole("button", { name: workflows.configArgumentsAsJson }))
    const json = screen.getByLabelText(workflows.configArgumentsJson) as HTMLTextAreaElement
    expect(JSON.parse(json.value)).toEqual({ query: "otters", limit: 5 })
    fireEvent.click(screen.getByRole("button", { name: workflows.configArgumentsAsFields }))
    expect(screen.getByLabelText(/^query/)).toBeTruthy()
  })

  it("when the server cannot be asked: says why, takes the tool name by hand, retries", async () => {
    const { ApiError } = (await import("@/lib/api")) as unknown as {
      ApiError: new (m: string, c: string, a: Record<string, unknown>) => Error
    }
    const listTools = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError("bad gateway", "mcp_tools_unavailable", {
          name: "Search",
          reason: "ConnectionError",
        }),
      )
      .mockResolvedValueOnce(TOOLS)
    backend.listTools = listTools
    const { setField } = renderMcp({ server_id: "s-1", tool_name: "", parameters: {} })
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toContain('MCP server "Search" did not answer (ConnectionError).')
    fireEvent.change(screen.getByPlaceholderText(workflows.configToolNamePlaceholder), {
      target: { value: "search" },
    })
    expect(setField).toHaveBeenLastCalledWith("tool_name", "search")
    expect(screen.getByLabelText(workflows.configArgumentsJson)).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: workflows.configToolsRetry }))
    await screen.findByRole("option", { name: "search" })
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull())
    expect(listTools).toHaveBeenCalledTimes(2)
  })

  it("flags a stored tool the server does not have", async () => {
    backend.listTools = () => Promise.resolve(TOOLS)
    renderMcp({ server_id: "s-1", tool_name: "gone", parameters: { a: 1 } })
    const alert = await screen.findByRole("alert")
    expect(alert.textContent).toBe('The server has no tool named "gone".')
    // Its arguments are still there, as JSON.
    const json = screen.getByLabelText(workflows.configArgumentsJson) as HTMLTextAreaElement
    expect(JSON.parse(json.value)).toEqual({ a: 1 })
  })
})

describe("MCP step form: the tool listing is kept for a short while", () => {
  const step = { server_id: "s-1", tool_name: "ping", parameters: {} }

  it("does not ask the server again when the same step is selected again", async () => {
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    // Selecting another step unmounts the form; coming back mounts it again.
    cleanup()
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    expect(backend.listTools).toHaveBeenCalledTimes(1)
  })

  it("asks again once the listing is older than its time", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000_000)
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    cleanup()
    now.mockReturnValue(1_000_000 + MCP_TOOLS_TTL_MS)
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    expect(backend.listTools).toHaveBeenCalledTimes(2)
  })

  it("does not show one user's listing to the next user of the same browser tab", async () => {
    window.localStorage.setItem("fim_user", JSON.stringify({ id: "u-1" }))
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    cleanup()
    window.localStorage.setItem("fim_user", JSON.stringify({ id: "u-2" }))
    renderMcp(step)
    await screen.findByRole("option", { name: "search" })
    expect(backend.listTools).toHaveBeenCalledTimes(2)
  })

  it("does not keep a failure, and Retry asks the server", async () => {
    backend.listTools = vi.fn(() => Promise.reject(new Error("down")))
    renderMcp(step)
    await screen.findByRole("button", { name: workflows.configToolsRetry })
    backend.listTools = vi.fn(() => Promise.resolve(TOOLS))
    fireEvent.click(screen.getByRole("button", { name: workflows.configToolsRetry }))
    await screen.findByRole("option", { name: "search" })
    expect(backend.listTools).toHaveBeenCalledTimes(1)
  })
})

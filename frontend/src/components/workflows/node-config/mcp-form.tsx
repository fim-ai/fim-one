"use client"

import { useEffect, useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import { RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { mcpServerApi, workflowApi } from "@/lib/api"
import { getErrorMessage } from "@/lib/error-utils"
import { InsertVariableBar, ResourceSelect, SectionHeader } from "./fields"
import {
  argumentFields,
  fromArgumentText,
  missingArguments,
  parseArgumentsJson,
  toArgumentText,
  toArgumentsJson,
  withArgument,
  type ArgumentField,
  type McpStepTool,
} from "./mcp-arguments"
import { forgetMcpTools, rememberMcpTools, rememberedMcpTools } from "./mcp-tools-memory"
import { resourceOptions } from "./resource-options"
import type { NodeFormProps } from "./types"
import { useResourceList } from "./use-resource-list"

/** What is known about the chosen server's tools. */
type ToolListing =
  | { state: "idle" }
  | { state: "loading"; serverId: string }
  | { state: "ready"; serverId: string; tools: McpStepTool[] }
  | { state: "failed"; serverId: string; message: string }

export function MCPForm({ data, setField, setFields, otherNodes }: NodeFormProps<"mcp">) {
  const t = useTranslations("workflows")
  const tError = useTranslations("errors")
  const serverId = data.server_id ?? ""
  const toolName = data.tool_name ?? ""
  const parameters = useMemo(
    () => (data.parameters ?? {}) as Record<string, unknown>,
    [data.parameters],
  )

  // MCP servers this user can use
  const servers = useResourceList(mcpServerApi.list)
  const serverChoices = useMemo(() => resourceOptions(servers, serverId), [servers, serverId])

  // The chosen server's tools. Asking opens the server, so it is done once
  // for a server, and again only on "Retry". An answer is kept for a short
  // while across mounts: selecting this step again does not ask again.
  const [listing, setListing] = useState<ToolListing>({ state: "idle" })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!serverId) return
    let cancelled = false
    const known = rememberedMcpTools(serverId)
    const answer = known
      ? Promise.resolve(known)
      : workflowApi.listStepMcpTools(serverId).then((tools) => {
          rememberMcpTools(serverId, tools)
          return tools
        })
    answer
      .then((tools) => {
        if (!cancelled) setListing({ state: "ready", serverId, tools })
      })
      .catch((err) => {
        if (!cancelled) {
          setListing({ state: "failed", serverId, message: getErrorMessage(err, tError) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [serverId, attempt, tError])
  // What is known about the server chosen now; an answer about another
  // server (the previous choice) counts as still loading.
  const current: ToolListing = !serverId
    ? { state: "idle" }
    : listing.state !== "idle" && listing.serverId === serverId
      ? listing
      : { state: "loading", serverId }

  const tools = current.state === "ready" ? current.tools : null
  const tool = tools?.find((candidate) => candidate.name === toolName) ?? null
  const fields = useMemo(() => (tool ? argumentFields(tool.input_schema) : null), [tool])
  const missing = fields ? missingArguments(fields, parameters) : []

  // Fields when the tool's schema gives them, JSON otherwise or on request.
  const [asJson, setAsJson] = useState(false)
  const showJson = asJson || fields === null
  // The JSON editor keeps what is typed, valid or not; `parameters` follows
  // only while it is a JSON object.
  // A draft holds for the parameters it was typed against: when they change
  // from elsewhere (undo, another field), the editor shows them again.
  const [draft, setJsonDraftState] = useState<{ text: string; over: string } | null>(null)
  const stored = JSON.stringify(parameters)
  const jsonDraft = draft !== null && draft.over === stored ? draft.text : null
  const setJsonDraft = (text: string | null, over: Record<string, unknown> = parameters) =>
    setJsonDraftState(text === null ? null : { text, over: JSON.stringify(over) })
  const jsonText = jsonDraft ?? toArgumentsJson(parameters)
  const jsonInvalid = jsonDraft !== null && parseArgumentsJson(jsonDraft) === null
  // The field a picked variable goes into: the last one that had focus.
  const [focused, setFocused] = useState<string | null>(null)

  const setArgument = (field: ArgumentField, text: string) => {
    setField("parameters", withArgument(parameters, field.name, fromArgumentText(text, field.type)))
  }

  const insertVariable = (reference: string) => {
    if (showJson) return
    const target = fields?.find((f) => f.name === focused) ?? fields?.[0]
    if (target) setArgument(target, toArgumentText(parameters[target.name]) + reference)
  }

  return (
    <div className="space-y-3">
      <ResourceSelect
        label={t("configServerId")}
        emptyLabel={t("configServerPlaceholder")}
        value={serverId}
        options={serverChoices}
        // A tool belongs to one server: a new server clears it.
        onChange={(v) => {
          setJsonDraft(null)
          setFields({ server_id: v, tool_name: "", parameters: {} })
        }}
        testId="mcp-server-select"
      />

      {/* Tool */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configToolName")}</label>
        {tools ? (
          <Select
            value={toolName || "__default__"}
            onValueChange={(v) => {
              const next = v === "__default__" ? "" : v
              if (next === toolName) return
              // Arguments belong to one tool: a new tool clears them.
              setJsonDraft(null)
              setFields({ tool_name: next, parameters: {} })
            }}
          >
            <SelectTrigger className="w-full h-7 text-xs" data-testid="mcp-tool-select">
              <SelectValue placeholder={t("configToolPlaceholder")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__default__">{t("configToolPlaceholder")}</SelectItem>
              {tools.map((candidate) => (
                <SelectItem key={candidate.name} value={candidate.name}>
                  {candidate.name}
                </SelectItem>
              ))}
              {toolName && !tool && <SelectItem value={toolName}>{toolName}</SelectItem>}
            </SelectContent>
          </Select>
        ) : (
          // Until the server has answered, and when it cannot be asked, the
          // tool is named by hand.
          <Input
            className="h-7 text-xs"
            placeholder={t("configToolNamePlaceholder")}
            value={toolName}
            onChange={(e) => setField("tool_name", e.target.value)}
          />
        )}
        {current.state === "loading" && (
          <p className="text-[10px] text-muted-foreground/60">{t("configToolsLoading")}</p>
        )}
        {current.state === "failed" && (
          <div className="flex items-start justify-between gap-2">
            <p className="text-xs text-destructive" role="alert">
              {t("configToolsFailed", { reason: current.message })}
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs gap-1 shrink-0"
              onClick={() => {
                forgetMcpTools(serverId)
                setListing({ state: "loading", serverId })
                setAttempt((n) => n + 1)
              }}
            >
              <RefreshCw className="h-3 w-3" />
              {t("configToolsRetry")}
            </Button>
          </div>
        )}
        {tools && toolName && !tool && (
          <p className="text-xs text-destructive" role="alert">
            {t("configToolNotOnServer", { tool: toolName })}
          </p>
        )}
        {tool?.description && (
          <p className="text-[10px] text-muted-foreground/60">{tool.description}</p>
        )}
      </div>

      {/* Arguments */}
      <SectionHeader label={t("configArguments")} />
      {fields !== null && (
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium">{t("configArguments")}</label>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-xs"
            onClick={() => {
              setJsonDraft(null)
              setAsJson((on) => !on)
            }}
          >
            {asJson ? t("configArgumentsAsFields") : t("configArgumentsAsJson")}
          </Button>
        </div>
      )}

      {showJson ? (
        <div className="space-y-1.5">
          <Textarea
            className="text-xs font-mono resize-none"
            rows={6}
            placeholder={t("configArgumentsJsonPlaceholder")}
            aria-label={t("configArgumentsJson")}
            aria-invalid={jsonInvalid}
            value={jsonText}
            onChange={(e) => {
              const parsed = parseArgumentsJson(e.target.value)
              setJsonDraft(e.target.value, parsed ?? parameters)
              if (parsed !== null) setField("parameters", parsed)
            }}
          />
          {jsonInvalid ? (
            <p className="text-sm text-destructive">{t("configArgumentsJsonInvalid")}</p>
          ) : (
            <p className="text-[10px] text-muted-foreground/60">
              {fields === null && toolName
                ? t("configArgumentsNoSchemaHint")
                : t("configArgumentsJsonHint")}
            </p>
          )}
        </div>
      ) : (
        <>
          {fields.length === 0 && (
            <p className="text-[10px] text-muted-foreground/60">{t("configArgumentsNone")}</p>
          )}
          {fields.map((field) => {
            const text = toArgumentText(parameters[field.name])
            const isMissing = missing.includes(field.name)
            return (
              <div key={field.name} className="space-y-1">
                <label className="text-xs font-medium font-mono" htmlFor={`mcp-arg-${field.name}`}>
                  {field.name}
                  {field.required && <span className="text-destructive"> *</span>}
                  <span className="ml-1.5 font-sans font-normal text-muted-foreground/60">
                    {field.type === "any" ? "" : field.type}
                  </span>
                </label>
                {field.options || field.type === "boolean" ? (
                  <Select
                    value={text === "" ? "__default__" : text}
                    onValueChange={(v) => setArgument(field, v === "__default__" ? "" : v)}
                  >
                    <SelectTrigger
                      id={`mcp-arg-${field.name}`}
                      className="w-full h-7 text-xs"
                      aria-invalid={isMissing}
                    >
                      <SelectValue placeholder={t("configArgumentUnset")} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__default__">{t("configArgumentUnset")}</SelectItem>
                      {(field.options ?? ["true", "false"]).map((option) => (
                        <SelectItem key={option} value={option}>
                          {option}
                        </SelectItem>
                      ))}
                      {text !== "" && !(field.options ?? ["true", "false"]).includes(text) && (
                        <SelectItem value={text}>{text}</SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                ) : field.type === "object" || field.type === "array" ? (
                  <Textarea
                    id={`mcp-arg-${field.name}`}
                    className="text-xs font-mono resize-none"
                    rows={3}
                    aria-invalid={isMissing}
                    value={text}
                    onFocus={() => setFocused(field.name)}
                    onChange={(e) => setArgument(field, e.target.value)}
                  />
                ) : (
                  <Input
                    id={`mcp-arg-${field.name}`}
                    className="h-7 text-xs"
                    aria-invalid={isMissing}
                    value={text}
                    onFocus={() => setFocused(field.name)}
                    onChange={(e) => setArgument(field, e.target.value)}
                  />
                )}
                {isMissing ? (
                  <p className="text-sm text-destructive">{t("configArgumentRequired")}</p>
                ) : (
                  field.description && (
                    <p className="text-[10px] text-muted-foreground/60">{field.description}</p>
                  )
                )}
              </div>
            )
          })}
          {fields.length > 0 && (
            <InsertVariableBar otherNodes={otherNodes} onInsert={insertVariable} />
          )}
        </>
      )}

      {/* Output variable */}
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configOutputVariable")}</label>
        <Input
          className="h-7 text-xs font-mono"
          value={data.output_variable ?? "mcp_result"}
          onChange={(e) => setField("output_variable", e.target.value)}
        />
      </div>
    </div>
  )
}

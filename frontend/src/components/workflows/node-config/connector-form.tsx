"use client"

import { useMemo } from "react"
import { useTranslations } from "next-intl"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { connectorApi } from "@/lib/api"
import {
  DATABASE_OPERATIONS,
  actionOptions,
  connectorOptions,
  databaseParameters,
  isDatabaseStep,
} from "./connector-options"
import { InsertVariableBar, OutputVariableField, SectionHeader } from "./fields"
import type { NodeFormProps } from "./types"
import { useResourceList } from "./use-resource-list"

export function ConnectorForm({ data, setField, setFields, otherNodes }: NodeFormProps<"connector">) {
  const t = useTranslations("workflows")
  const { user } = useAuth()
  const parameters = (data.parameters ?? {}) as Record<string, string>
  const paramEntries = Object.entries(parameters)
  const connectorId = data.connector_id ?? ""
  const actionId = data.action_id ?? ""

  // Connectors this user can use, with their actions
  const connectors = useResourceList(connectorApi.list)
  const connectorChoices = useMemo(
    () => connectorOptions(connectors, connectorId, user?.id ?? null),
    [connectors, connectorId, user?.id],
  )
  // A database connector has operations in place of actions.
  const isDatabase = isDatabaseStep(connectors, connectorId, actionId)
  const actionChoices = useMemo(
    () => actionOptions(connectors, connectorId, actionId),
    [connectors, connectorId, actionId],
  )

  const addParam = () => {
    setField("parameters", { ...parameters, "": "" })
  }

  const removeParam = (key: string) => {
    const updated = { ...parameters }
    delete updated[key]
    setField("parameters", updated)
  }

  const updateParam = (oldKey: string, newKey: string, value: string) => {
    const updated: Record<string, string> = {}
    for (const [k, v] of Object.entries(parameters)) {
      if (k === oldKey) {
        updated[newKey] = value
      } else {
        updated[k] = v
      }
    }
    setField("parameters", updated)
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label className="text-xs font-medium">{t("configSelectConnector")}</label>
        <Select
          value={connectorId || "__default__"}
          onValueChange={(v) => {
            const next = v === "__default__" ? "" : v
            if (next === connectorId) return
            // An action belongs to one connector: a new connector clears it.
            // Parameters go too when the kind changes: an action's
            // parameters mean nothing to a database, and the other way round.
            const nextIsDatabase = connectors.find((c) => c.id === next)?.type === "database"
            setFields(
              nextIsDatabase === isDatabase
                ? { connector_id: next, action_id: "" }
                : { connector_id: next, action_id: "", parameters: {} },
            )
          }}
        >
          <SelectTrigger className="w-full h-7 text-xs">
            <SelectValue placeholder={t("configConnectorPlaceholder")} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__default__">{t("configConnectorPlaceholder")}</SelectItem>
            {connectorChoices.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {isDatabase ? (
        <>
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configSelectOperation")}</label>
            <Select
              value={actionId || "__default__"}
              onValueChange={(v) => {
                const next = v === "__default__" ? "" : v
                if (next === actionId) return
                setFields({ action_id: next, parameters: databaseParameters(next, parameters) })
              }}
            >
              <SelectTrigger className="w-full h-7 text-xs" data-testid="connector-operation">
                <SelectValue placeholder={t("configOperationPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default__">{t("configOperationPlaceholder")}</SelectItem>
                {DATABASE_OPERATIONS.map((operation) => (
                  <SelectItem key={operation} value={operation}>
                    {t(`configOperation_${operation}`)}
                  </SelectItem>
                ))}
                {actionId && !(DATABASE_OPERATIONS as readonly string[]).includes(actionId) && (
                  <SelectItem value={actionId}>{actionId}</SelectItem>
                )}
              </SelectContent>
            </Select>
            <p className="text-[10px] text-muted-foreground/60">{t("configDatabaseOwnerHint")}</p>
          </div>

          {actionId === "describe_table" && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t("configTableName")}</label>
              <Input
                className="h-7 text-xs font-mono"
                placeholder={t("configTableNamePlaceholder")}
                value={parameters.table_name ?? ""}
                onChange={(e) => setField("parameters", { table_name: e.target.value })}
              />
              <InsertVariableBar
                otherNodes={otherNodes}
                onInsert={(ref) =>
                  setField("parameters", { table_name: (parameters.table_name ?? "") + ref })
                }
              />
            </div>
          )}

          {actionId === "query" && (
            <div className="space-y-1.5">
              <label className="text-xs font-medium">{t("configSql")}</label>
              <Textarea
                className="text-xs font-mono resize-none"
                rows={5}
                placeholder={t("configSqlPlaceholder")}
                value={parameters.sql ?? ""}
                onChange={(e) => setField("parameters", { sql: e.target.value })}
              />
              <InsertVariableBar
                otherNodes={otherNodes}
                onInsert={(ref) => setField("parameters", { sql: (parameters.sql ?? "") + ref })}
              />
              <p className="text-[10px] text-muted-foreground/60">{t("configSqlHint")}</p>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="space-y-1.5">
            <label className="text-xs font-medium">{t("configSelectAction")}</label>
            <Select
              value={actionId || "__default__"}
              onValueChange={(v) => setField("action_id", v === "__default__" ? "" : v)}
            >
              <SelectTrigger className="w-full h-7 text-xs">
                <SelectValue placeholder={t("configActionPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__default__">{t("configActionPlaceholder")}</SelectItem>
                {actionChoices.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Parameter mapping */}
          <SectionHeader label={t("configParameters")} />
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium">{t("configParameters")}</label>
            <Button variant="ghost" size="sm" className="h-6 text-xs gap-1" onClick={addParam}>
              <Plus className="h-3 w-3" />
              {t("configAddParam")}
            </Button>
          </div>
          {paramEntries.map(([key, value], i) => (
            <div key={i} className="flex items-center gap-2">
              <Input
                className="h-7 text-xs flex-1"
                placeholder={t("configKey")}
                value={key}
                onChange={(e) => updateParam(key, e.target.value, value)}
              />
              <Input
                className="h-7 text-xs flex-1"
                placeholder={t("configValue")}
                value={value}
                onChange={(e) => updateParam(key, key, e.target.value)}
              />
              <Button variant="ghost" size="icon-sm" onClick={() => removeParam(key)}>
                <Trash2 className="h-3 w-3 text-destructive" />
              </Button>
            </div>
          ))}
          <InsertVariableBar
            otherNodes={otherNodes}
            onInsert={(ref) => {
              // For connector params, append to last param value if exists
              const paramEntries = Object.entries((data.parameters ?? {}) as Record<string, string>)
              if (paramEntries.length > 0) {
                const [lastKey] = paramEntries[paramEntries.length - 1]
                const updated: Record<string, string> = {}
                for (const [k, v] of paramEntries) {
                  updated[k] = k === lastKey ? v + ref : v
                }
                setField("parameters", updated)
              }
            }}
          />
        </>
      )}

      <OutputVariableField
        value={data.output_variable}
        onChange={(v) => setField("output_variable", v)}
      />
    </div>
  )
}

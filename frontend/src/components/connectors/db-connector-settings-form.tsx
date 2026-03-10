"use client"

import { useState, useEffect } from "react"
import { Loader2, Plug, CheckCircle2, XCircle, AlertTriangle } from "lucide-react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Switch } from "@/components/ui/switch"
import { EmojiPickerPopover } from "@/components/ui/emoji-picker-popover"
import { connectorApi } from "@/lib/api"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { ConnectorResponse, DbConnectionConfig } from "@/types/connector"

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DB_DRIVERS = [
  { value: "postgresql", labelKey: "dbTypePostgresql", port: 5432 },
  { value: "mysql", labelKey: "dbTypeMysql", port: 3306 },
  { value: "oracle", labelKey: "dbTypeOracle", port: 1521 },
  { value: "sqlserver", labelKey: "dbTypeSqlserver", port: 1433 },
  { value: "dm8", labelKey: "dbTypeDm8", port: 5236 },
  { value: "kingbasees", labelKey: "dbTypeKingbasees", port: 54321 },
  { value: "gbase", labelKey: "dbTypeGbase", port: 5258 },
  { value: "highgo", labelKey: "dbTypeHighgo", port: 5866 },
] as const

// Drivers that support schema field (PG-compatible)
const SCHEMA_DRIVERS = new Set(["postgresql", "oracle", "kingbasees", "highgo"])

function getDefaultPort(driver: string): number {
  return DB_DRIVERS.find((d) => d.value === driver)?.port ?? 5432
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface DbConnectorSettingsFormProps {
  connector: ConnectorResponse | null // null = create mode
  onSaved: (connector: ConnectorResponse) => void
  onDirtyChange?: (dirty: boolean) => void
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function DbConnectorSettingsForm({
  connector,
  onSaved,
  onDirtyChange,
}: DbConnectorSettingsFormProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")

  // Basic fields
  const [name, setName] = useState("")
  const [icon, setIcon] = useState<string | null>(null)
  const [description, setDescription] = useState("")

  // DB config fields
  const [driver, setDriver] = useState("postgresql")
  const [host, setHost] = useState("")
  const [port, setPort] = useState(5432)
  const [database, setDatabase] = useState("")
  const [dbSchema, setDbSchema] = useState("")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [ssl, setSsl] = useState(false)
  const [readOnly, setReadOnly] = useState(true)
  const [maxRows, setMaxRows] = useState(1000)
  const [queryTimeout, setQueryTimeout] = useState(30)

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [testResult, setTestResult] = useState<{
    success: boolean
    db_version: string | null
    error: string | null
  } | null>(null)

  // Pre-fill when connector prop changes
  useEffect(() => {
    if (connector) {
      setName(connector.name)
      setIcon(connector.icon || null)
      setDescription(connector.description || "")
      const cfg = connector.db_config
      if (cfg) {
        setDriver(cfg.driver)
        setHost(cfg.host)
        setPort(cfg.port)
        setDatabase(cfg.database)
        setDbSchema(cfg.schema || "")
        setUsername(cfg.username)
        setPassword(cfg.password || "")
        setSsl(cfg.ssl)
        setReadOnly(cfg.read_only)
        setMaxRows(cfg.max_rows)
        setQueryTimeout(cfg.query_timeout)
      }
    } else {
      setName("")
      setIcon(null)
      setDescription("")
      setDriver("postgresql")
      setHost("")
      setPort(5432)
      setDatabase("")
      setDbSchema("")
      setUsername("")
      setPassword("")
      setSsl(false)
      setReadOnly(true)
      setMaxRows(1000)
      setQueryTimeout(30)
    }
    setTestResult(null)
  }, [connector])

  // Compute and notify dirty state
  useEffect(() => {
    if (!onDirtyChange) return
    if (!connector) {
      onDirtyChange(name.trim() !== "" || host.trim() !== "")
      return
    }
    const cfg = connector.db_config
    const dirty =
      name !== connector.name ||
      icon !== (connector.icon || null) ||
      description !== (connector.description || "") ||
      driver !== (cfg?.driver ?? "postgresql") ||
      host !== (cfg?.host ?? "") ||
      port !== (cfg?.port ?? 5432) ||
      database !== (cfg?.database ?? "") ||
      dbSchema !== (cfg?.schema ?? "") ||
      username !== (cfg?.username ?? "") ||
      (password !== "" && password !== (cfg?.password ?? "")) ||
      ssl !== (cfg?.ssl ?? false) ||
      readOnly !== (cfg?.read_only ?? true) ||
      maxRows !== (cfg?.max_rows ?? 1000) ||
      queryTimeout !== (cfg?.query_timeout ?? 30)
    onDirtyChange(dirty)
  }, [
    connector, name, icon, description, driver, host, port, database,
    dbSchema, username, password, ssl, readOnly, maxRows, queryTimeout,
    onDirtyChange,
  ])

  const handleDriverChange = (value: string) => {
    setDriver(value)
    setPort(getDefaultPort(value))
    if (!SCHEMA_DRIVERS.has(value)) {
      setDbSchema("")
    }
    setTestResult(null)
  }

  const handleTestConnection = async () => {
    if (!connector?.id) return
    setIsTesting(true)
    setTestResult(null)
    try {
      const result = await connectorApi.testConnection(connector.id)
      setTestResult(result)
      if (result.success) {
        toast.success(t("connectionSuccess"))
      } else {
        toast.error(t("connectionFailed"))
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error"
      setTestResult({ success: false, db_version: null, error: message })
      toast.error(t("connectionFailed"))
    } finally {
      setIsTesting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedHost = host.trim()
    const trimmedDatabase = database.trim()
    if (!trimmedName || !trimmedHost || !trimmedDatabase) return

    setIsSubmitting(true)
    try {
      const dbConfig: DbConnectionConfig = {
        driver,
        host: trimmedHost,
        port,
        database: trimmedDatabase,
        ...(SCHEMA_DRIVERS.has(driver) && dbSchema.trim() && { schema: dbSchema.trim() }),
        username: username.trim(),
        ...(password.trim() && { password: password.trim() }),
        ssl,
        read_only: readOnly,
        max_rows: maxRows,
        query_timeout: queryTimeout,
      }

      let result: ConnectorResponse
      if (connector) {
        result = await connectorApi.update(connector.id, {
          name: trimmedName,
          icon: icon || null,
          description: description.trim() || null,
          type: "database",
          db_config: dbConfig,
        })
      } else {
        result = await connectorApi.createDbConnector({
          name: trimmedName,
          icon: icon || null,
          description: description.trim() || null,
          type: "database",
          db_config: dbConfig,
        })
      }

      onSaved(result)
      setTestResult(null)
      toast.success(connector ? t("connectorUpdated") : t("connectorCreated"))
    } catch (err) {
      console.error("Failed to save connector:", err)
      const message = err instanceof Error ? err.message : "Unknown error"
      toast.error(t("connectorSaveFailed", { message }))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col h-full overflow-hidden">
      <ScrollArea className="flex-1">
        <div className="space-y-4">
          {/* Name + Icon */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-name" className="text-sm font-medium">
              {tc("name")} <span className="text-destructive">*</span>
            </label>
            <div className="flex items-center gap-2">
              <EmojiPickerPopover
                value={icon}
                onChange={setIcon}
                fallbackIcon={<Plug className="h-5 w-5" />}
              />
              <Input
                id="db-connector-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
                required
              />
            </div>
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-description" className="text-sm font-medium">
              {tc("description")}
            </label>
            <Textarea
              id="db-connector-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("descriptionPlaceholder")}
              rows={2}
              className="resize-none"
            />
          </div>

          {/* Database Type */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-driver" className="text-sm font-medium">
              {t("databaseType")} <span className="text-destructive">*</span>
            </label>
            <Select value={driver} onValueChange={handleDriverChange}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DB_DRIVERS.map((d) => (
                  <SelectItem key={d.value} value={d.value}>
                    {t(d.labelKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Host + Port */}
          <div className="grid grid-cols-[1fr_120px] gap-3">
            <div className="space-y-1.5">
              <label htmlFor="db-connector-host" className="text-sm font-medium">
                {t("host")} <span className="text-destructive">*</span>
              </label>
              <Input
                id="db-connector-host"
                type="text"
                value={host}
                onChange={(e) => { setHost(e.target.value); setTestResult(null) }}
                placeholder="localhost"
                required
              />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="db-connector-port" className="text-sm font-medium">
                {t("port")}
              </label>
              <Input
                id="db-connector-port"
                type="number"
                value={port}
                onChange={(e) => { setPort(Number(e.target.value)); setTestResult(null) }}
                min={1}
                max={65535}
              />
            </div>
          </div>

          {/* Database Name */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-database" className="text-sm font-medium">
              {t("databaseName")} <span className="text-destructive">*</span>
            </label>
            <Input
              id="db-connector-database"
              type="text"
              value={database}
              onChange={(e) => { setDatabase(e.target.value); setTestResult(null) }}
              placeholder="my_database"
              required
            />
          </div>

          {/* Schema (PG-compatible only) */}
          {SCHEMA_DRIVERS.has(driver) && (
            <div className="space-y-1.5">
              <label htmlFor="db-connector-schema" className="text-sm font-medium">
                {t("dbSchema")}
              </label>
              <Input
                id="db-connector-schema"
                type="text"
                value={dbSchema}
                onChange={(e) => setDbSchema(e.target.value)}
                placeholder="public"
              />
            </div>
          )}

          {/* Username */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-username" className="text-sm font-medium">
              {t("username")}
            </label>
            <Input
              id="db-connector-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="admin"
            />
          </div>

          {/* Password */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-password" className="text-sm font-medium">
              {t("password")}
            </label>
            <Input
              id="db-connector-password"
              type="password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setTestResult(null) }}
              placeholder="********"
            />
          </div>

          {/* SSL toggle */}
          <div className="flex items-center justify-between">
            <label htmlFor="db-connector-ssl" className="text-sm font-medium">
              {t("ssl")}
            </label>
            <Switch
              id="db-connector-ssl"
              checked={ssl}
              onCheckedChange={setSsl}
            />
          </div>

          {/* Read Only toggle */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label htmlFor="db-connector-read-only" className="text-sm font-medium">
                {t("readOnly")}
              </label>
              <Switch
                id="db-connector-read-only"
                checked={readOnly}
                onCheckedChange={setReadOnly}
              />
            </div>
            {!readOnly && (
              <p className="text-xs text-amber-600 dark:text-amber-400 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3 shrink-0" />
                {t("readOnlyWarning")}
              </p>
            )}
          </div>

          {/* Max Rows */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-max-rows" className="text-sm font-medium">
              {t("maxRows")}
            </label>
            <Input
              id="db-connector-max-rows"
              type="number"
              value={maxRows}
              onChange={(e) => setMaxRows(Number(e.target.value))}
              min={1}
              max={10000}
            />
          </div>

          {/* Query Timeout */}
          <div className="space-y-1.5">
            <label htmlFor="db-connector-timeout" className="text-sm font-medium">
              {t("queryTimeout")}
            </label>
            <Input
              id="db-connector-timeout"
              type="number"
              value={queryTimeout}
              onChange={(e) => setQueryTimeout(Number(e.target.value))}
              min={1}
              max={300}
            />
          </div>

          {/* Test Connection */}
          {connector?.id && (
            <div className="space-y-2 rounded-md border border-border p-3">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleTestConnection}
                disabled={isTesting}
                className="gap-1.5"
              >
                {isTesting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {t("testConnection")}
              </Button>
              {testResult && (
                <div className="text-sm">
                  {testResult.success ? (
                    <p className="text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
                      <CheckCircle2 className="h-4 w-4 shrink-0" />
                      {t("connectionSuccess")}
                      {testResult.db_version && (
                        <span className="text-muted-foreground">
                          {" \u2014 "}{t("dbVersion")}: {testResult.db_version}
                        </span>
                      )}
                    </p>
                  ) : (
                    <p className="text-destructive flex items-start gap-1.5">
                      <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
                      <span>
                        {t("connectionFailed")}
                        {testResult.error && (
                          <span className="block text-xs text-muted-foreground mt-0.5">
                            {testResult.error}
                          </span>
                        )}
                      </span>
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Save button outside scroll area */}
      <div className="flex justify-end pt-4">
        <Button
          type="submit"
          disabled={isSubmitting || !name.trim() || !host.trim() || !database.trim()}
        >
          {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
          {tc("save")}
        </Button>
      </div>
    </form>
  )
}

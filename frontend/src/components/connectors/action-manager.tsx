"use client"

import { useState } from "react"
import { Plus, Trash2, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { connectorApi } from "@/lib/api"
import { toast } from "sonner"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type {
  ConnectorResponse,
  ConnectorActionResponse,
  ConnectorActionCreate,
} from "@/types/connector"

// ---------------------------------------------------------------------------
// Shared constants
// ---------------------------------------------------------------------------

const METHODS = ["GET", "POST", "PUT", "DELETE"] as const

const METHOD_COLORS: Record<string, string> = {
  GET: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  POST: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  PUT: "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  DELETE: "bg-red-500/15 text-red-600 dark:text-red-400",
}

interface ActionFormState {
  name: string
  description: string
  method: string
  path: string
  parametersSchema: string
  responseExtract: string
  requiresConfirmation: boolean
}

const EMPTY_FORM: ActionFormState = {
  name: "",
  description: "",
  method: "GET",
  path: "",
  parametersSchema: "",
  responseExtract: "",
  requiresConfirmation: false,
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ActionManagerProps {
  connector: ConnectorResponse
  onChanged: () => void // called after any CRUD operation to refresh parent
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function ActionManager({ connector, onChanged }: ActionManagerProps) {
  const t = useTranslations("connectors")
  const tc = useTranslations("common")

  const [selectedActionId, setSelectedActionId] = useState<string | null>(null)
  const [isAddingNew, setIsAddingNew] = useState(false)
  const [form, setForm] = useState<ActionFormState>(EMPTY_FORM)
  const [editingActionId, setEditingActionId] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)

  // Derived: whether the right panel shows a form
  const showForm = isAddingNew || editingActionId !== null

  // ------- Handlers -------

  const resetForm = () => {
    setForm(EMPTY_FORM)
    setEditingActionId(null)
    setIsAddingNew(false)
    setSelectedActionId(null)
  }

  const handleAddNew = () => {
    setForm(EMPTY_FORM)
    setEditingActionId(null)
    setSelectedActionId(null)
    setIsAddingNew(true)
  }

  const handleSelectAction = (action: ConnectorActionResponse) => {
    setSelectedActionId(action.id)
    setIsAddingNew(false)
    setEditingActionId(action.id)
    setForm({
      name: action.name,
      description: action.description || "",
      method: action.method,
      path: action.path,
      parametersSchema: action.parameters_schema
        ? JSON.stringify(action.parameters_schema, null, 2)
        : "",
      responseExtract: action.response_extract || "",
      requiresConfirmation: action.requires_confirmation,
    })
  }

  const handleDeleteAction = async (actionId: string) => {
    setDeletingId(actionId)
    try {
      await connectorApi.deleteAction(connector.id, actionId)
      // If the deleted action was selected, clear the form
      if (selectedActionId === actionId || editingActionId === actionId) {
        resetForm()
      }
      onChanged()
      toast.success(t("actionDeleted"))
    } catch {
      toast.error(t("actionDeleteFailed"))
    } finally {
      setDeletingId(null)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = form.name.trim()
    const trimmedPath = form.path.trim()
    if (!trimmedName || !trimmedPath) return

    setIsSubmitting(true)
    try {
      let parsedSchema: Record<string, unknown> | null = null
      if (form.parametersSchema.trim()) {
        try {
          parsedSchema = JSON.parse(form.parametersSchema.trim())
        } catch {
          console.error("Invalid JSON in parameters schema")
          setIsSubmitting(false)
          return
        }
      }

      const body: ConnectorActionCreate = {
        name: trimmedName,
        description: form.description.trim() || null,
        method: form.method,
        path: trimmedPath,
        parameters_schema: parsedSchema,
        response_extract: form.responseExtract.trim() || null,
        requires_confirmation: form.requiresConfirmation,
      }

      if (editingActionId && !isAddingNew) {
        await connectorApi.updateAction(connector.id, editingActionId, body)
      } else {
        await connectorApi.createAction(connector.id, body)
      }

      toast.success(isAddingNew || !editingActionId ? t("actionCreated") : t("actionUpdated"))
      resetForm()
      onChanged()
    } catch {
      toast.error(t("actionSaveFailed"))
    } finally {
      setIsSubmitting(false)
    }
  }

  // ------- Render -------

  return (
    <div className="flex-1 flex min-h-0">
      {/* ---- Left panel: action list ---- */}
      <div className="w-[250px] border-r flex flex-col min-h-0 overflow-hidden">
        <div className="p-3 border-b">
          <Button
            variant="outline"
            size="sm"
            onClick={handleAddNew}
            className="gap-1.5 w-full"
          >
            <Plus className="h-4 w-4" />
            {t("addAction")}
          </Button>
        </div>

        <ScrollArea className="flex-1 min-h-0">
          <div className="p-2 space-y-1">
            {connector.actions.length === 0 && (
              <p className="text-xs text-muted-foreground text-center py-6">
                {t("noActionsYet")}
              </p>
            )}

            {connector.actions.map((action) => (
              <div
                key={action.id}
                role="button"
                tabIndex={0}
                onClick={() => handleSelectAction(action)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault()
                    handleSelectAction(action)
                  }
                }}
                className={cn(
                  "group flex items-start gap-2 rounded-md border px-2.5 py-2 cursor-pointer transition-colors",
                  selectedActionId === action.id
                    ? "bg-accent border-border"
                    : "border-transparent hover:bg-muted/50",
                )}
              >
                <span
                  className={cn(
                    "text-[10px] font-semibold w-12 text-center py-0.5 rounded shrink-0 mt-0.5",
                    METHOD_COLORS[action.method] || "bg-muted text-muted-foreground",
                  )}
                >
                  {action.method}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{action.name}</p>
                  <p className="text-xs text-muted-foreground truncate">
                    {action.path}
                  </p>
                </div>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={(e) => {
                        e.stopPropagation()
                        setPendingDeleteId(action.id)
                      }}
                      disabled={deletingId === action.id}
                      className="shrink-0 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity"
                    >
                      {deletingId === action.id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" sideOffset={5}>{t("deleteAction")}</TooltipContent>
                </Tooltip>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* ---- Right panel: form or empty state ---- */}
      <div className="flex-1 flex flex-col">
        {!showForm ? (
          <div className="flex-1 flex items-center justify-center">
            <p className="text-sm text-muted-foreground">
              {t("selectOrCreateAction")}
            </p>
          </div>
        ) : (
          <ScrollArea className="flex-1">
            <form onSubmit={handleSubmit} className="p-4 space-y-3">
              <p className="text-sm font-medium mb-2">
                {editingActionId && !isAddingNew ? t("editAction") : t("newAction")}
              </p>

              {/* Name */}
              <div className="space-y-1.5">
                <label htmlFor="am-action-name" className="text-sm font-medium">
                  {tc("name")} <span className="text-destructive">*</span>
                </label>
                <Input
                  id="am-action-name"
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder={t("actionNamePlaceholder")}
                  required
                />
              </div>

              {/* Description */}
              <div className="space-y-1.5">
                <label htmlFor="am-action-description" className="text-sm font-medium">
                  {tc("description")}
                </label>
                <Textarea
                  id="am-action-description"
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder={t("actionDescriptionPlaceholder")}
                  rows={2}
                  className="resize-none"
                />
              </div>

              {/* Method + Path */}
              <div className="grid grid-cols-[120px_1fr] gap-3">
                <div className="space-y-1.5">
                  <label htmlFor="am-action-method" className="text-sm font-medium">
                    {t("method")}
                  </label>
                  <Select value={form.method} onValueChange={(v) => setForm({ ...form, method: v })}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {METHODS.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <label htmlFor="am-action-path" className="text-sm font-medium">
                    {t("path")} <span className="text-destructive">*</span>
                  </label>
                  <Input
                    id="am-action-path"
                    type="text"
                    value={form.path}
                    onChange={(e) => setForm({ ...form, path: e.target.value })}
                    placeholder="/repos/{owner}/{repo}"
                    required
                  />
                </div>
              </div>

              {/* Parameters Schema */}
              <div className="space-y-1.5">
                <label htmlFor="am-action-params" className="text-sm font-medium">
                  {t("parametersSchema")}
                </label>
                <Textarea
                  id="am-action-params"
                  value={form.parametersSchema}
                  onChange={(e) =>
                    setForm({ ...form, parametersSchema: e.target.value })
                  }
                  placeholder='{"owner": {"type": "string", "required": true}}'
                  rows={3}
                  className="resize-y font-mono text-xs"
                />
              </div>

              {/* Response Extract */}
              <div className="space-y-1.5">
                <label htmlFor="am-action-extract" className="text-sm font-medium">
                  {t("responseExtract")}
                </label>
                <Input
                  id="am-action-extract"
                  type="text"
                  value={form.responseExtract}
                  onChange={(e) =>
                    setForm({ ...form, responseExtract: e.target.value })
                  }
                  placeholder="data[].{name: name, id: id}"
                />
                <p className="text-xs text-muted-foreground">
                  {t("responseExtractHelp")}
                </p>
              </div>

              {/* Requires Confirmation */}
              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <Input
                  type="checkbox"
                  checked={form.requiresConfirmation}
                  onChange={(e) =>
                    setForm({ ...form, requiresConfirmation: e.target.checked })
                  }
                  className="h-3.5 w-3.5 rounded border-input accent-primary"
                />
                <span>{t("requiresConfirmation")}</span>
              </label>

              {/* Form buttons */}
              <div className="flex justify-end gap-2 pt-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={resetForm}
                  disabled={isSubmitting}
                >
                  {tc("cancel")}
                </Button>
                <Button
                  type="submit"
                  size="sm"
                  disabled={
                    isSubmitting || !form.name.trim() || !form.path.trim()
                  }
                >
                  {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  {editingActionId && !isAddingNew ? tc("update") : tc("add")}
                </Button>
              </div>
            </form>
          </ScrollArea>
        )}
      </div>
      {/* ---- Delete confirmation dialog ---- */}
      <Dialog
        open={pendingDeleteId !== null}
        onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteActionTitle")}</DialogTitle>
            <DialogDescription>
              {t("deleteActionDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>
              {tc("cancel")}
            </Button>
            <Button
              variant="destructive"
              className="px-6"
              onClick={() => {
                if (pendingDeleteId) {
                  handleDeleteAction(pendingDeleteId)
                }
                setPendingDeleteId(null)
              }}
            >
              {tc("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

"use client"

import { useState } from "react"
import { Loader2, Plus, Pencil, Trash2, X } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { connectorApi } from "@/lib/api"
import type { ConnectorResponse, ConnectorActionResponse, ConnectorActionCreate } from "@/types/connector"

interface ActionEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  connector: ConnectorResponse
  onActionCreated: () => void // refresh parent
}

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

export function ActionEditorDialog({
  open,
  onOpenChange,
  connector,
  onActionCreated,
}: ActionEditorDialogProps) {
  const [showForm, setShowForm] = useState(false)
  const [editingActionId, setEditingActionId] = useState<string | null>(null)
  const [form, setForm] = useState<ActionFormState>(EMPTY_FORM)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const resetForm = () => {
    setForm(EMPTY_FORM)
    setEditingActionId(null)
    setShowForm(false)
  }

  const handleAddNew = () => {
    setForm(EMPTY_FORM)
    setEditingActionId(null)
    setShowForm(true)
  }

  const handleEditAction = (action: ConnectorActionResponse) => {
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
    setEditingActionId(action.id)
    setShowForm(true)
  }

  const handleDeleteAction = async (actionId: string) => {
    setDeletingId(actionId)
    try {
      await connectorApi.deleteAction(connector.id, actionId)
      onActionCreated()
    } catch (err) {
      console.error("Failed to delete action:", err)
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

      if (editingActionId) {
        await connectorApi.updateAction(connector.id, editingActionId, body)
      } else {
        await connectorApi.createAction(connector.id, body)
      }

      resetForm()
      onActionCreated()
    } catch (err) {
      console.error("Failed to save action:", err)
    } finally {
      setIsSubmitting(false)
    }
  }

  const inputClass =
    "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) resetForm(); onOpenChange(v) }}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Manage Actions — {connector.name}
          </DialogTitle>
        </DialogHeader>

        {/* Existing actions list */}
        <div className="space-y-2">
          {connector.actions.length === 0 && !showForm && (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No actions yet. Add your first action to define API endpoints.
            </p>
          )}

          {connector.actions.map((action) => (
            <div
              key={action.id}
              className="flex items-center gap-3 rounded-md border border-border px-3 py-2"
            >
              <span
                className={cn(
                  "text-[10px] font-semibold px-1.5 py-0.5 rounded",
                  METHOD_COLORS[action.method] || "bg-muted text-muted-foreground",
                )}
              >
                {action.method}
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{action.name}</p>
                <p className="text-xs text-muted-foreground truncate">{action.path}</p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => handleEditAction(action)}
                  className="text-muted-foreground hover:text-foreground"
                  title="Edit Action"
                >
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={() => handleDeleteAction(action.id)}
                  disabled={deletingId === action.id}
                  className="text-muted-foreground hover:text-destructive"
                  title="Delete Action"
                >
                  {deletingId === action.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            </div>
          ))}
        </div>

        {/* Add action button */}
        {!showForm && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleAddNew}
            className="gap-1.5 w-full"
          >
            <Plus className="h-4 w-4" />
            Add Action
          </Button>
        )}

        {/* Inline action form */}
        {showForm && (
          <form onSubmit={handleSubmit} className="space-y-3 border border-border rounded-md p-4">
            <div className="flex items-center justify-between mb-1">
              <p className="text-sm font-medium">
                {editingActionId ? "Edit Action" : "New Action"}
              </p>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={resetForm}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>

            {/* Name */}
            <div className="space-y-1.5">
              <label htmlFor="action-name" className="text-sm font-medium">
                Name <span className="text-destructive">*</span>
              </label>
              <input
                id="action-name"
                type="text"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="List Repositories"
                required
                className={inputClass}
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <label htmlFor="action-description" className="text-sm font-medium">
                Description
              </label>
              <textarea
                id="action-description"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="What does this action do..."
                rows={2}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
              />
            </div>

            {/* Method + Path on same row */}
            <div className="grid grid-cols-[120px_1fr] gap-3">
              <div className="space-y-1.5">
                <label htmlFor="action-method" className="text-sm font-medium">
                  Method
                </label>
                <select
                  id="action-method"
                  value={form.method}
                  onChange={(e) => setForm({ ...form, method: e.target.value })}
                  className={inputClass}
                >
                  {METHODS.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <label htmlFor="action-path" className="text-sm font-medium">
                  Path <span className="text-destructive">*</span>
                </label>
                <input
                  id="action-path"
                  type="text"
                  value={form.path}
                  onChange={(e) => setForm({ ...form, path: e.target.value })}
                  placeholder="/repos/{owner}/{repo}"
                  required
                  className={inputClass}
                />
              </div>
            </div>

            {/* Parameters Schema */}
            <div className="space-y-1.5">
              <label htmlFor="action-params" className="text-sm font-medium">
                Parameters Schema (JSON)
              </label>
              <textarea
                id="action-params"
                value={form.parametersSchema}
                onChange={(e) => setForm({ ...form, parametersSchema: e.target.value })}
                placeholder='{"owner": {"type": "string", "required": true}}'
                rows={3}
                className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y font-mono text-xs"
              />
            </div>

            {/* Response Extract */}
            <div className="space-y-1.5">
              <label htmlFor="action-extract" className="text-sm font-medium">
                Response Extract (JMESPath)
              </label>
              <input
                id="action-extract"
                type="text"
                value={form.responseExtract}
                onChange={(e) => setForm({ ...form, responseExtract: e.target.value })}
                placeholder="data[].{name: name, id: id}"
                className={inputClass}
              />
              <p className="text-xs text-muted-foreground">
                JMESPath expression to extract relevant data from the API response.
              </p>
            </div>

            {/* Requires Confirmation */}
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.requiresConfirmation}
                onChange={(e) => setForm({ ...form, requiresConfirmation: e.target.checked })}
                className="h-3.5 w-3.5 rounded border-input accent-primary"
              />
              <span>Requires user confirmation before execution</span>
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
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={isSubmitting || !form.name.trim() || !form.path.trim()}
              >
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                {editingActionId ? "Update" : "Add"}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}

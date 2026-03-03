"use client"

import { useState, useEffect } from "react"
import { Check, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { kbApi, connectorApi } from "@/lib/api"
import type { ConnectorResponse } from "@/types/connector"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import type { AgentCreate, AgentResponse } from "@/types/agent"

const TOOL_CATEGORIES = ["computation", "web", "filesystem", "knowledge", "mcp", "connector", "general"]

interface AgentFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  agent: AgentResponse | null
  onSubmit: (data: AgentCreate) => Promise<void>
  isSubmitting: boolean
}

export function AgentFormDialog({
  open,
  onOpenChange,
  agent,
  onSubmit,
  isSubmitting,
}: AgentFormDialogProps) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [instructions, setInstructions] = useState("")
  const [toolCategories, setToolCategories] = useState<string[]>([])
  const [suggestedPrompts, setSuggestedPrompts] = useState("")
  const [selectedKBs, setSelectedKBs] = useState<string[]>([])
  const [availableKBs, setAvailableKBs] = useState<{id: string; name: string; document_count: number}[]>([])
  const [availableConnectors, setAvailableConnectors] = useState<ConnectorResponse[]>([])
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([])
  const [confidenceThreshold, setConfidenceThreshold] = useState<number | null>(null)

  // Pre-fill when editing or reset when creating
  useEffect(() => {
    if (!open) return
    if (agent) {
      setName(agent.name)
      setDescription(agent.description || "")
      setInstructions(agent.instructions || "")
      setToolCategories(agent.tool_categories || [])
      setSuggestedPrompts(agent.suggested_prompts?.join("\n") || "")
      setSelectedKBs(agent.kb_ids || [])
      setSelectedConnectors(agent.connector_ids || [])
      const ct = agent.grounding_config?.confidence_threshold
      setConfidenceThreshold(typeof ct === "number" ? ct : null)
    } else {
      setName("")
      setDescription("")
      setInstructions("")
      setToolCategories([])
      setSuggestedPrompts("")
      setSelectedKBs([])
      setSelectedConnectors([])
      setConfidenceThreshold(null)
    }
  }, [open, agent])

  useEffect(() => {
    if (!open) return
    kbApi
      .list(1, 100)
      .then((d) => setAvailableKBs(d.items || []))
      .catch(() => setAvailableKBs([]))
    connectorApi
      .list(1, 100)
      .then((d) => setAvailableConnectors(d.items || []))
      .catch(() => setAvailableConnectors([]))
  }, [open])

  const toggleCategory = (cat: string) => {
    setToolCategories((prev) =>
      prev.includes(cat)
        ? prev.filter((c) => c !== cat)
        : [...prev, cat]
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return

    const prompts = suggestedPrompts
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)

    const data: AgentCreate = {
      name: trimmedName,
      description: description.trim() || null,
      instructions: instructions.trim() || null,
      tool_categories: toolCategories,
      ...(prompts.length > 0 && { suggested_prompts: prompts }),
      ...(selectedKBs.length > 0 && { kb_ids: selectedKBs }),
      ...(selectedConnectors.length > 0 && { connector_ids: selectedConnectors }),
      ...(selectedKBs.length > 0 && confidenceThreshold != null && {
        grounding_config: { confidence_threshold: confidenceThreshold },
      }),
    }

    await onSubmit(data)
  }

  const isEditing = agent !== null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEditing ? "Edit Agent" : "Create Agent"}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-1.5">
            <label htmlFor="agent-name" className="text-sm font-medium">
              Name <span className="text-destructive">*</span>
            </label>
            <input
              id="agent-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Agent"
              required
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            />
          </div>

          {/* Description */}
          <div className="space-y-1.5">
            <label htmlFor="agent-description" className="text-sm font-medium">
              Description
            </label>
            <textarea
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief description of what this agent does..."
              rows={2}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
            />
          </div>

          {/* Instructions */}
          <div className="space-y-1.5">
            <label htmlFor="agent-instructions" className="text-sm font-medium">
              Instructions
            </label>
            <textarea
              id="agent-instructions"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="System prompt for the agent. Tell it how to behave, what to focus on..."
              rows={5}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-y"
            />
          </div>

          {/* Tool Categories */}
          <div className="space-y-1.5">
            <label className="text-sm font-medium">Tool Categories</label>
            <div className="flex flex-wrap gap-2">
              {TOOL_CATEGORIES.map((cat) => (
                <label
                  key={cat}
                  className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                >
                  <input
                    type="checkbox"
                    checked={toolCategories.includes(cat)}
                    onChange={() => toggleCategory(cat)}
                    className="h-3.5 w-3.5 rounded border-input accent-primary"
                  />
                  <span className="text-muted-foreground">{cat}</span>
                </label>
              ))}
            </div>
          </div>

          {/* Knowledge Bases */}
          {(availableKBs.length > 0 || selectedKBs.some((id) => !availableKBs.some((kb) => kb.id === id))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Knowledge Bases</label>
              <p className="text-xs text-muted-foreground">
                Bind KBs to enable evidence-grounded retrieval with citations
              </p>
              <div className="flex flex-col gap-1.5">
                {availableKBs.map((kb) => {
                  const isChecked = selectedKBs.includes(kb.id)
                  const toggleKB = () =>
                    setSelectedKBs((prev) =>
                      prev.includes(kb.id)
                        ? prev.filter((id) => id !== kb.id)
                        : [...prev, kb.id]
                    )
                  return (
                    <div
                      key={kb.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleKB}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleKB() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {kb.name} ({kb.document_count} docs)
                      </span>
                    </div>
                  )
                })}
                {selectedKBs
                  .filter((id) => !availableKBs.some((kb) => kb.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedKBs((prev) => prev.filter((id) => id !== orphanId))
                    return (
                      <div
                        key={orphanId}
                        role="checkbox"
                        aria-checked={true}
                        tabIndex={0}
                        onClick={toggleOrphan}
                        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleOrphan() } }}
                        className="flex items-center gap-1.5 text-sm cursor-pointer select-none rounded px-1 py-0.5 bg-destructive/10"
                      >
                        <div className="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors bg-primary border-primary">
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        </div>
                        <span className="text-destructive/80 truncate max-w-[200px]" title={orphanId}>
                          {orphanId.length > 12 ? `${orphanId.slice(0, 12)}...` : orphanId}
                        </span>
                        <span className="text-destructive/60 text-xs">(deleted)</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Connectors */}
          {(availableConnectors.length > 0 || selectedConnectors.some((id) => !availableConnectors.some((c) => c.id === id))) && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Connectors</label>
              <p className="text-xs text-muted-foreground">
                Bind connectors to give the agent access to external API actions
              </p>
              <div className="flex flex-col gap-1.5">
                {availableConnectors.map((conn) => {
                  const isChecked = selectedConnectors.includes(conn.id)
                  const toggleConn = () =>
                    setSelectedConnectors((prev) =>
                      prev.includes(conn.id)
                        ? prev.filter((id) => id !== conn.id)
                        : [...prev, conn.id]
                    )
                  return (
                    <div
                      key={conn.id}
                      role="checkbox"
                      aria-checked={isChecked}
                      tabIndex={0}
                      onClick={toggleConn}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleConn() } }}
                      className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                    >
                      <div className={`h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors ${isChecked ? "bg-primary border-primary" : "border-input"}`}>
                        {isChecked && <Check className="h-2.5 w-2.5 text-primary-foreground" />}
                      </div>
                      <span className="text-muted-foreground">
                        {conn.name} ({conn.actions.length} actions)
                      </span>
                    </div>
                  )
                })}
                {selectedConnectors
                  .filter((id) => !availableConnectors.some((c) => c.id === id))
                  .map((orphanId) => {
                    const toggleOrphan = () =>
                      setSelectedConnectors((prev) => prev.filter((id) => id !== orphanId))
                    return (
                      <div
                        key={orphanId}
                        role="checkbox"
                        aria-checked={true}
                        tabIndex={0}
                        onClick={toggleOrphan}
                        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleOrphan() } }}
                        className="flex items-center gap-1.5 text-sm cursor-pointer select-none rounded px-1 py-0.5 bg-destructive/10"
                      >
                        <div className="h-3.5 w-3.5 rounded border flex items-center justify-center transition-colors bg-primary border-primary">
                          <Check className="h-2.5 w-2.5 text-primary-foreground" />
                        </div>
                        <span className="text-destructive/80 truncate max-w-[200px]" title={orphanId}>
                          {orphanId.length > 12 ? `${orphanId.slice(0, 12)}...` : orphanId}
                        </span>
                        <span className="text-destructive/60 text-xs">(deleted)</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Confidence Threshold - only show when KBs are selected */}
          {selectedKBs.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium">Confidence Threshold</label>
                <span className="text-xs text-muted-foreground font-mono">
                  {confidenceThreshold != null
                    ? `${Math.round(confidenceThreshold * 100)}%`
                    : "Off"}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">
                When set, answers with confidence below this threshold will be
                rejected. Leave off to always show results.
              </p>
              <div className="flex items-center gap-3">
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={
                    confidenceThreshold != null
                      ? Math.round(confidenceThreshold * 100)
                      : 0
                  }
                  onChange={(e) => {
                    const val = parseInt(e.target.value)
                    setConfidenceThreshold(val === 0 ? null : val / 100)
                  }}
                  className="flex-1 h-1.5 accent-primary"
                />
              </div>
            </div>
          )}


          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
              {isEditing ? "Save Changes" : "Create Agent"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

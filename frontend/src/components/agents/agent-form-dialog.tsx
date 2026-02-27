"use client"

import { useState, useEffect } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import type { AgentCreate, AgentResponse } from "@/types/agent"

const TOOL_CATEGORIES = ["computation", "web", "filesystem", "knowledge", "mcp", "general"]

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
  const [executionMode, setExecutionMode] = useState<"react" | "dag">("react")
  const [toolCategories, setToolCategories] = useState<string[]>([])
  const [suggestedPrompts, setSuggestedPrompts] = useState("")
  const [selectedKBs, setSelectedKBs] = useState<string[]>([])
  const [availableKBs, setAvailableKBs] = useState<{id: string; name: string; document_count: number}[]>([])

  // Pre-fill when editing or reset when creating
  useEffect(() => {
    if (!open) return
    if (agent) {
      setName(agent.name)
      setDescription(agent.description || "")
      setInstructions(agent.instructions || "")
      setExecutionMode(agent.execution_mode as "react" | "dag")
      setToolCategories(agent.tool_categories || [])
      setSuggestedPrompts(agent.suggested_prompts?.join("\n") || "")
      setSelectedKBs(agent.kb_ids || [])
    } else {
      setName("")
      setDescription("")
      setInstructions("")
      setExecutionMode("react")
      setToolCategories([])
      setSuggestedPrompts("")
      setSelectedKBs([])
    }
  }, [open, agent])

  useEffect(() => {
    if (!open) return
    const token = localStorage.getItem("token")
    if (!token) return
    fetch("/api/knowledge-bases?size=100", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => r.json())
      .then((d) => setAvailableKBs(d.items || []))
      .catch(() => setAvailableKBs([]))
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
      ...(description.trim() && { description: description.trim() }),
      ...(instructions.trim() && { instructions: instructions.trim() }),
      execution_mode: executionMode,
      ...(toolCategories.length > 0 && { tool_categories: toolCategories }),
      ...(prompts.length > 0 && { suggested_prompts: prompts }),
      ...(selectedKBs.length > 0 && { kb_ids: selectedKBs }),
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

          {/* Execution Mode */}
          <div className="space-y-1.5">
            <label htmlFor="agent-mode" className="text-sm font-medium">
              Execution Mode
            </label>
            <select
              id="agent-mode"
              value={executionMode}
              onChange={(e) => setExecutionMode(e.target.value as "react" | "dag")}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="react">ReAct</option>
              <option value="dag">DAG</option>
            </select>
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
          {availableKBs.length > 0 && (
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Knowledge Bases</label>
              <p className="text-xs text-muted-foreground">
                Bind KBs to enable evidence-grounded retrieval with citations
              </p>
              <div className="flex flex-col gap-1.5">
                {availableKBs.map((kb) => (
                  <label
                    key={kb.id}
                    className="flex items-center gap-1.5 text-sm cursor-pointer select-none"
                  >
                    <input
                      type="checkbox"
                      checked={selectedKBs.includes(kb.id)}
                      onChange={() =>
                        setSelectedKBs((prev) =>
                          prev.includes(kb.id)
                            ? prev.filter((id) => id !== kb.id)
                            : [...prev, kb.id]
                        )
                      }
                      className="h-3.5 w-3.5 rounded border-input accent-primary"
                    />
                    <span className="text-muted-foreground">
                      {kb.name} ({kb.document_count} docs)
                    </span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Suggested Prompts */}
          <div className="space-y-1.5">
            <label htmlFor="agent-prompts" className="text-sm font-medium">
              Suggested Prompts
            </label>
            <textarea
              id="agent-prompts"
              value={suggestedPrompts}
              onChange={(e) => setSuggestedPrompts(e.target.value)}
              placeholder={"One prompt per line\nE.g. Summarize this document\nWhat are the key findings?"}
              rows={3}
              className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring resize-none"
            />
          </div>

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

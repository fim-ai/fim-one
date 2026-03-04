"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { toast } from "sonner"
import { Plus, Loader2, Bot, Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAuth } from "@/contexts/auth-context"
import { agentApi } from "@/lib/api"
import { AgentCard } from "@/components/agents/agent-card"
import type { AgentResponse } from "@/types/agent"

export default function AgentsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadAgents = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await agentApi.list()
      setAgents(data.items)
    } catch (err) {
      console.error("Failed to load agents:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadAgents()
  }, [user, loadAgents])

  const handleDelete = (id: string) => setPendingDeleteId(id)
  const handlePublish = (id: string) => setPendingPublishId(id)
  const handleUnpublish = (id: string) => setPendingUnpublishId(id)

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await agentApi.delete(id)
      setAgents((prev) => prev.filter((a) => a.id !== id))
      toast.success("Agent deleted")
    } catch {
      toast.error("Failed to delete agent")
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await agentApi.publish(id)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success("Agent published")
    } catch {
      toast.error("Failed to publish agent")
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await agentApi.unpublish(id)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success("Agent unpublished")
    } catch {
      toast.error("Failed to unpublish agent")
    }
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Bot className="h-5 w-5" />
            Agents
          </h1>
          <p className="text-sm text-muted-foreground">
            Create and manage your AI agents
          </p>
        </div>
        <Button size="sm" className="gap-1.5" asChild>
          <Link href="/agents/new">
            <Plus className="h-4 w-4" />
            New Agent
          </Link>
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : agents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <p className="text-sm text-muted-foreground">
              No agents yet. Create your first agent to get started.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
              asChild
            >
              <Link href="/agents/new">
                <Plus className="h-4 w-4" />
                Create Agent
              </Link>
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onDelete={handleDelete}
                onPublish={handlePublish}
                onUnpublish={handleUnpublish}
              />
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              Delete agent?
            </DialogTitle>
            <DialogDescription>
              This agent will be permanently deleted. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>Cancel</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Publish Confirmation */}
      <Dialog open={pendingPublishId !== null} onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Publish agent?</DialogTitle>
            <DialogDescription>
              Once published, this agent will be available for use in conversations.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingPublishId(null)}>Cancel</Button>
            <Button className="px-6" onClick={confirmPublish}>Publish</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish Confirmation */}
      <Dialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Unpublish agent?</DialogTitle>
            <DialogDescription>
              This agent will be set back to draft and no longer available in conversations.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingUnpublishId(null)}>Cancel</Button>
            <Button variant="secondary" className="px-6" onClick={confirmUnpublish}>Unpublish</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

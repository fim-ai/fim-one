"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { Plus, Loader2, Plug, Trash2 } from "lucide-react"
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
import { connectorApi } from "@/lib/api"
import { ConnectorCard } from "@/components/connectors/connector-card"
import type { ConnectorResponse } from "@/types/connector"
import { toast } from "sonner"

export default function ConnectorsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [connectors, setConnectors] = useState<ConnectorResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  const loadConnectors = useCallback(async () => {
    try {
      setIsLoading(true)
      const data = await connectorApi.list()
      setConnectors(data.items)
    } catch (err) {
      console.error("Failed to load connectors:", err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) loadConnectors()
  }, [user, loadConnectors])

  const handleDelete = (id: string) => setPendingDeleteId(id)

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await connectorApi.delete(id)
      setConnectors((prev) => prev.filter((c) => c.id !== id))
      toast.success("Connector deleted")
    } catch {
      toast.error("Failed to delete connector")
    }
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground flex items-center gap-2">
            <Plug className="h-5 w-5" />
            Connectors
          </h1>
          <p className="text-sm text-muted-foreground">
            Manage API connectors and their actions
          </p>
        </div>
        <Button size="sm" className="gap-1.5" asChild>
          <Link href="/connectors/new">
            <Plus className="h-4 w-4" />
            New Connector
          </Link>
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : connectors.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <p className="text-sm text-muted-foreground">
              No connectors yet. Create your first one to connect external APIs.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
              asChild
            >
              <Link href="/connectors/new">
                <Plus className="h-4 w-4" />
                Create Connector
              </Link>
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {connectors.map((connector) => (
              <ConnectorCard
                key={connector.id}
                connector={connector}
                onDelete={handleDelete}
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
              Delete connector?
            </DialogTitle>
            <DialogDescription>
              This connector and all its actions will be permanently deleted. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>Cancel</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

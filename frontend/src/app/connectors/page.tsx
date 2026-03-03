"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import { Plus, Loader2 } from "lucide-react"
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
import { ConnectorFormDialog } from "@/components/connectors/connector-form-dialog"
import { ActionEditorDialog } from "@/components/connectors/action-editor-dialog"
import type { ConnectorResponse, ConnectorCreate, ConnectorUpdate } from "@/types/connector"

export default function ConnectorsPage() {
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [connectors, setConnectors] = useState<ConnectorResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingConnector, setEditingConnector] = useState<ConnectorResponse | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [managingActionsConnector, setManagingActionsConnector] = useState<ConnectorResponse | null>(null)

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

  const handleCreate = () => {
    setEditingConnector(null)
    setDialogOpen(true)
  }

  const handleEdit = (connector: ConnectorResponse) => {
    setEditingConnector(connector)
    setDialogOpen(true)
  }

  const handleSubmit = async (data: ConnectorCreate | ConnectorUpdate) => {
    setIsSubmitting(true)
    try {
      if (editingConnector) {
        await connectorApi.update(editingConnector.id, data)
      } else {
        await connectorApi.create(data as ConnectorCreate)
      }
      setDialogOpen(false)
      await loadConnectors()
    } catch (err) {
      console.error("Failed to save connector:", err)
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDelete = (id: string) => setPendingDeleteId(id)

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await connectorApi.delete(id)
      setConnectors((prev) => prev.filter((c) => c.id !== id))
    } catch (err) {
      console.error("Failed to delete connector:", err)
    }
  }

  const handleManageActions = (connector: ConnectorResponse) => {
    setManagingActionsConnector(connector)
  }

  const handleTogglePublish = (connector: ConnectorResponse) => {
    if (connector.status === "published") {
      setPendingUnpublishId(connector.id)
    } else {
      setPendingPublishId(connector.id)
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await connectorApi.publish(id)
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
    } catch (err) {
      console.error("Failed to publish connector:", err)
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await connectorApi.unpublish(id)
      setConnectors((prev) => prev.map((c) => (c.id === id ? updated : c)))
    } catch (err) {
      console.error("Failed to unpublish connector:", err)
    }
  }

  const handleActionCreated = async () => {
    // Reload connectors to get updated action lists
    await loadConnectors()
    // Also refresh the managing connector reference
    if (managingActionsConnector) {
      try {
        const updated = await connectorApi.get(managingActionsConnector.id)
        setManagingActionsConnector(updated)
      } catch {
        // connector may have been deleted
        setManagingActionsConnector(null)
      }
    }
  }

  if (authLoading || !user) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-border/40">
        <div>
          <h1 className="text-lg font-semibold text-foreground">Connectors</h1>
          <p className="text-sm text-muted-foreground">
            Manage API connectors and their actions
          </p>
        </div>
        <Button onClick={handleCreate} size="sm" className="gap-1.5">
          <Plus className="h-4 w-4" />
          New Connector
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
              onClick={handleCreate}
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
            >
              <Plus className="h-4 w-4" />
              Create Connector
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {connectors.map((connector) => (
              <ConnectorCard
                key={connector.id}
                connector={connector}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onManageActions={handleManageActions}
                onTogglePublish={handleTogglePublish}
              />
            ))}
          </div>
        )}
      </div>

      {/* Form Dialog */}
      <ConnectorFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        connector={editingConnector}
        onSubmit={handleSubmit}
        isSubmitting={isSubmitting}
      />

      {/* Action Editor Dialog */}
      {managingActionsConnector && (
        <ActionEditorDialog
          open={managingActionsConnector !== null}
          onOpenChange={(open) => { if (!open) setManagingActionsConnector(null) }}
          connector={managingActionsConnector}
          onActionCreated={handleActionCreated}
        />
      )}

      {/* Delete Confirmation */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete connector?</DialogTitle>
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

      {/* Publish Confirmation */}
      <Dialog open={pendingPublishId !== null} onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Publish connector?</DialogTitle>
            <DialogDescription>
              Once published, this connector will be available for agents to use in conversations.
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
            <DialogTitle>Unpublish connector?</DialogTitle>
            <DialogDescription>
              This connector will be set back to draft and no longer available for agents to use.
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

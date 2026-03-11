"use client"

import { useState, useEffect, useCallback } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { useTranslations } from "next-intl"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { agentApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { AgentCard } from "@/components/agents/agent-card"
import type { AgentResponse } from "@/types/agent"

export default function AgentsPage() {
  const t = useTranslations("agents")
  const tc = useTranslations("common")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()

  const [agents, setAgents] = useState<AgentResponse[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [pendingPublishId, setPendingPublishId] = useState<string | null>(null)
  const [pendingUnpublishId, setPendingUnpublishId] = useState<string | null>(null)
  const [publishScope, setPublishScope] = useState<"personal" | "org">("personal")
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)

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
      setAgents((data.items as AgentResponse[]).filter((a) => !a.is_builder))
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
  const handlePublish = (id: string) => {
    setPendingPublishId(id)
    setPublishScope("personal")
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }
  const handleUnpublish = (id: string) => setPendingUnpublishId(id)

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    try {
      await agentApi.delete(id)
      setAgents((prev) => prev.filter((a) => a.id !== id))
      toast.success(t("agentDeleted"))
    } catch {
      toast.error(t("agentDeleteFailed"))
    }
  }

  const confirmPublish = async () => {
    if (!pendingPublishId) return
    const id = pendingPublishId
    setPendingPublishId(null)
    try {
      const updated = await agentApi.publish(id, {
        scope: publishScope,
        org_id: publishScope === "org" ? publishOrgId : undefined,
      } as { scope: "personal" | "org" | "global"; org_id?: string })
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success(t("agentPublished"))
    } catch {
      toast.error(t("agentPublishFailed"))
    }
  }

  const confirmUnpublish = async () => {
    if (!pendingUnpublishId) return
    const id = pendingUnpublishId
    setPendingUnpublishId(null)
    try {
      const updated = await agentApi.unpublish(id)
      setAgents((prev) => prev.map((a) => (a.id === id ? updated : a)))
      toast.success(t("agentUnpublished"))
    } catch {
      toast.error(t("agentUnpublishFailed"))
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
            {t("title")}
          </h1>
          <p className="text-sm text-muted-foreground">
            {t("subtitle")}
          </p>
        </div>
        <Button size="sm" className="gap-1.5" asChild>
          <Link href="/agents/new">
            <Plus className="h-4 w-4" />
            {t("newAgent")}
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
              {t("emptyState")}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4 gap-1.5"
              asChild
            >
              <Link href="/agents/new">
                <Plus className="h-4 w-4" />
                {t("createAgent")}
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
              {t("deleteDialogTitle")}
            </DialogTitle>
            <DialogDescription>
              {t("deleteDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>{tc("cancel")}</Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>{tc("delete")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Publish Confirmation */}
      <Dialog open={pendingPublishId !== null} onOpenChange={(open) => { if (!open) setPendingPublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("publishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("publishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label className="text-sm font-medium">{t("publishScopeLabel")}</Label>
              <div className="flex gap-2">
                {(["personal", "org"] as const).map((scope) => (
                  <button
                    key={scope}
                    type="button"
                    onClick={() => {
                      setPublishScope(scope)
                      if (scope === "org" && userOrgs.length > 0) setPublishOrgId(userOrgs[0].id)
                    }}
                    className={`flex-1 rounded-md border px-3 py-1.5 text-sm transition-colors ${
                      publishScope === scope
                        ? "border-primary bg-primary/10 text-primary font-medium"
                        : "border-input text-muted-foreground hover:border-foreground/30"
                    }`}
                  >
                    {scope === "personal" ? t("publishScopePersonal") : t("publishScopeOrg")}
                  </button>
                ))}
              </div>
            </div>
            {publishScope === "org" && (
              <div className="space-y-2">
                {orgsLoading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  </div>
                ) : userOrgs.length === 0 ? (
                  <p className="text-sm text-muted-foreground">{t("publishNoOrgs")}</p>
                ) : (
                  <Select value={publishOrgId} onValueChange={setPublishOrgId}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t("publishSelectOrg")} />
                    </SelectTrigger>
                    <SelectContent>
                      {userOrgs.map((org) => (
                        <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingPublishId(null)}>{tc("cancel")}</Button>
            <Button
              className="px-6"
              onClick={confirmPublish}
              disabled={publishScope === "org" && (orgsLoading || userOrgs.length === 0 || !publishOrgId)}
            >
              {tc("publish")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish Confirmation */}
      <Dialog open={pendingUnpublishId !== null} onOpenChange={(open) => { if (!open) setPendingUnpublishId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unpublishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("unpublishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingUnpublishId(null)}>{tc("cancel")}</Button>
            <Button variant="secondary" className="px-6" onClick={confirmUnpublish}>{tc("unpublish")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

"use client"

import { useState, useEffect, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Plus, Star, Loader2, Trash2, MoreHorizontal, Check, Pencil, MessagesSquare, Search, MessageSquare } from "lucide-react"
import { cn } from "@/lib/utils"
import { EmptyState } from "@/components/shared/empty-state"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import Link from "next/link"
import { Skeleton } from "@/components/ui/skeleton"
import { conversationApi } from "@/lib/api"
import { useConversation } from "@/contexts/conversation-context"
import type { ConversationResponse } from "@/types/conversation"
import { usePageTitle } from "@/hooks/use-page-title"

function formatRelativeTime(dateStr: string, t: (key: string, values?: Record<string, number>) => string): string {
  const now = Date.now()
  const d = new Date(dateStr).getTime()
  const diff = now - d
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return t("justNow")
  if (mins < 60) return t("minutesAgo", { count: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t("hoursAgo", { count: hours })
  const days = Math.floor(hours / 24)
  if (days < 30) return t("daysAgo", { count: days })
  const months = Math.floor(days / 30)
  return t("monthsAgo", { count: months })
}

function sortConversations(convs: ConversationResponse[]): ConversationResponse[] {
  const starred = convs.filter((c) => c.starred).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  const unstarred = convs.filter((c) => !c.starred).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )
  return [...starred, ...unstarred]
}

export default function ChatsPage() {
  const t = useTranslations("auth")
  const tl = useTranslations("layout")
  const tc = useTranslations("common")
  const { loadConversations } = useConversation()

  usePageTitle(tl("allChats"))

  const [conversations, setConversations] = useState<ConversationResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")

  const [selectMode, setSelectMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // Single delete
  const [singleDeleteId, setSingleDeleteId] = useState<string | null>(null)

  // Rename dialog
  const [renameDialogOpen, setRenameDialogOpen] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 300)
    return () => clearTimeout(timer)
  }, [searchQuery])

  // Fetch conversations
  const fetchPage = useCallback(async (pageNum: number, query: string, append: boolean) => {
    if (append) setLoadingMore(true)
    else setLoading(true)

    try {
      const res = await conversationApi.list(pageNum, 20, query || undefined)
      if (append) {
        setConversations((prev) => [...prev, ...res.items])
      } else {
        setConversations(res.items)
      }
      setTotal(res.total)
      setPage(pageNum)
    } catch (err) {
      console.error("Failed to load conversations:", err)
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [])

  // Reset and fetch on query change
  useEffect(() => {
    fetchPage(1, debouncedQuery, false)
  }, [debouncedQuery, fetchPage])

  const handleShowMore = () => {
    fetchPage(page + 1, debouncedQuery, true)
  }

  const handleStarToggle = async (e: React.MouseEvent, conv: ConversationResponse) => {
    e.stopPropagation()
    try {
      const updated = await conversationApi.update(conv.id, { starred: !conv.starred })
      setConversations((prev) =>
        prev.map((c) => (c.id === conv.id ? { ...c, starred: updated.starred } : c)),
      )
      loadConversations()
    } catch (err) {
      console.error("Failed to toggle star:", err)
    }
  }

  const handleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleBatchDelete = async () => {
    if (selectedIds.size === 0) return
    setDeleting(true)
    try {
      const ids = Array.from(selectedIds)
      await conversationApi.batchDelete(ids)
      setSelectedIds(new Set())
      setSelectMode(false)
      setDeleteDialogOpen(false)
      loadConversations()
      // Refetch page 1 so the main content area shows remaining data
      await fetchPage(1, debouncedQuery, false)
    } catch (err) {
      console.error("Failed to batch delete:", err)
    } finally {
      setDeleting(false)
    }
  }


  const handleSingleDelete = async () => {
    if (!singleDeleteId) return
    setDeleting(true)
    try {
      await conversationApi.delete(singleDeleteId)
      setSingleDeleteId(null)
      setDeleteDialogOpen(false)
      loadConversations()
      // Refetch page 1 so the main content area shows remaining data
      await fetchPage(1, debouncedQuery, false)
    } catch (err) {
      console.error("Failed to delete conversation:", err)
    } finally {
      setDeleting(false)
    }
  }

  const handleRenameSubmit = async () => {
    if (!renamingId || !renameValue.trim()) return
    try {
      const updated = await conversationApi.update(renamingId, { title: renameValue.trim() })
      setConversations((prev) =>
        prev.map((c) => (c.id === renamingId ? { ...c, title: updated.title } : c)),
      )
      setRenameDialogOpen(false)
      setRenamingId(null)
      loadConversations()
    } catch (err) {
      console.error("Failed to rename conversation:", err)
    }
  }

  const exitSelectMode = () => {
    setSelectMode(false)
    setSelectedIds(new Set())
  }

  const sorted = sortConversations(conversations)
  const hasMore = conversations.length < total

  return (
    <div className="flex h-full flex-col overflow-hidden">
    <div className="flex-1 overflow-y-auto px-6 py-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <MessagesSquare className="h-6 w-6" />
          {t("chatsTitle")}
        </h1>
        <Button size="sm" variant="outline" className="gap-2" asChild>
          <Link href="/new">
            <Plus className="h-4 w-4" />
            {t("newChat")}
          </Link>
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t("searchConversations")}
          className="w-full"
        />
      </div>

      {/* Count + Select toggle */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">
            {t("chatCount", { count: total })}
          </span>
          {selectMode && sorted.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs text-muted-foreground"
              onClick={() => {
                if (selectedIds.size === sorted.length) {
                  setSelectedIds(new Set())
                } else {
                  setSelectedIds(new Set(sorted.map((c) => c.id)))
                }
              }}
            >
              {selectedIds.size === sorted.length ? tc("deselectAll") : tc("selectAll")}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          {selectMode && selectedIds.size > 0 && (
            <Button
              size="sm"
              variant="destructive"
              className="gap-1.5"
              onClick={() => setDeleteDialogOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t("deleteCount", { count: selectedIds.size })}
            </Button>
          )}
          {selectMode ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={exitSelectMode}
            >
              {tc("cancel")}
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSelectMode(true)}
            >
              {t("select")}
            </Button>
          )}
        </div>
      </div>

      {/* Conversation list */}
      {loading ? (
        <div className="space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton.ListRow key={i} />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        debouncedQuery ? (
          <EmptyState
            icon={<Search />}
            title={tc("noResultsTitle")}
            description={tc("noResultsDescription")}
          />
        ) : (
          <EmptyState
            icon={<MessageSquare />}
            title={t("noConversationsTitle")}
            description={t("noConversationsDescription")}
          />
        )
      ) : (
        <div className="space-y-1">
          {sorted.map((conv) => (
            <Link
              key={conv.id}
              href={`/?c=${conv.id}`}
              onClick={(e) => {
                if (selectMode) {
                  e.preventDefault()
                  setSelectedIds((prev) => {
                    const next = new Set(prev)
                    if (next.has(conv.id)) next.delete(conv.id)
                    else next.add(conv.id)
                    return next
                  })
                }
              }}
              className={cn(
                "group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors cursor-pointer",
                selectedIds.has(conv.id)
                  ? "bg-accent"
                  : "hover:bg-accent/50",
              )}
            >
              {selectMode && (
                <input
                  type="checkbox"
                  checked={selectedIds.has(conv.id)}
                  readOnly
                  className="h-4 w-4 shrink-0 rounded border-border accent-primary pointer-events-none"
                />
              )}
              {conv.starred && (
                <Star className="h-4 w-4 shrink-0 fill-current text-yellow-500" />
              )}
              <span className="flex-1 truncate text-sm">
                {conv.title || t("untitled")}
              </span>
              <span className="text-xs text-muted-foreground shrink-0">
                {formatRelativeTime(conv.created_at, t)}
              </span>
              {!selectMode && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                    <button className="shrink-0 p-1 rounded-md opacity-0 group-hover:opacity-100 hover:bg-accent transition-opacity text-muted-foreground">
                      <MoreHorizontal className="h-4 w-4" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-40">
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation()
                      setSelectMode(true)
                      setSelectedIds(new Set([conv.id]))
                    }}>
                      <Check className="h-4 w-4 mr-2" />
                      {t("select")}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation()
                      handleStarToggle(e as unknown as React.MouseEvent, conv)
                    }}>
                      <Star className={cn("h-4 w-4 mr-2", conv.starred && "fill-current text-yellow-500")} />
                      {conv.starred ? t("unstar") : t("star")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation()
                      setRenamingId(conv.id)
                      setRenameValue(conv.title || "")
                      setRenameDialogOpen(true)
                    }}>
                      <Pencil className="h-4 w-4 mr-2" />
                      {t("rename")}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        setSingleDeleteId(conv.id)
                        setDeleteDialogOpen(true)
                      }}
                    >
                      <Trash2 className="h-4 w-4 mr-2" />
                      {tc("delete")}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </Link>
          ))}
        </div>
      )}

      {/* Show more */}
      {hasMore && !loading && (
        <div className="mt-4 text-center">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleShowMore}
            disabled={loadingMore}
          >
            {loadingMore ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
                {tc("loading")}
              </>
            ) : (
              tc("showMore")
            )}
          </Button>
        </div>
      )}

      {/* Delete confirmation dialog (single + batch) */}
      <Dialog open={deleteDialogOpen} onOpenChange={(open) => {
        setDeleteDialogOpen(open)
        if (!open) setSingleDeleteId(null)
      }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="h-4 w-4" />
              {singleDeleteId
                ? t("deleteSingleTitle")
                : t("deleteBatchTitle", { count: selectedIds.size })}
            </DialogTitle>
            <DialogDescription>
              {tc("confirmDeleteDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => { setDeleteDialogOpen(false); setSingleDeleteId(null) }}>
              {tc("cancel")}
            </Button>
            <Button
              variant="destructive"
              className="px-6"
              onClick={singleDeleteId ? handleSingleDelete : handleBatchDelete}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  {tc("deleting")}
                </>
              ) : (
                tc("delete")
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Rename dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={(open) => {
        setRenameDialogOpen(open)
        if (!open) setRenamingId(null)
      }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Pencil className="h-4 w-4" />
              {t("renameConversation")}
            </DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleRenameSubmit() }}
            placeholder={t("conversationTitlePlaceholder")}
          />
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => { setRenameDialogOpen(false); setRenamingId(null) }}>
              {tc("cancel")}
            </Button>
            <Button className="px-6" onClick={handleRenameSubmit} disabled={!renameValue.trim()}>
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
    </div>
  )
}

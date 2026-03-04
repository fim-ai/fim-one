"use client"

import { useState, useEffect, useCallback } from "react"
import { Plus, Star, Loader2, Trash2, MoreHorizontal, Check, Pencil, MessagesSquare } from "lucide-react"
import { cn } from "@/lib/utils"
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
import { conversationApi } from "@/lib/api"
import { useConversation } from "@/contexts/conversation-context"
import type { ConversationResponse } from "@/types/conversation"

function formatRelativeTime(dateStr: string): string {
  const now = Date.now()
  const d = new Date(dateStr).getTime()
  const diff = now - d
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  const months = Math.floor(days / 30)
  return `${months}mo ago`
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
  const { loadConversations } = useConversation()

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
    <div className="h-full overflow-y-auto">
    <div className="max-w-3xl mx-auto py-8 px-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <MessagesSquare className="h-6 w-6" />
          Chats
        </h1>
        <Button size="sm" variant="outline" className="gap-2" asChild>
          <Link href="/new">
            <Plus className="h-4 w-4" />
            New Chat
          </Link>
        </Button>
      </div>

      {/* Search */}
      <div className="mb-4">
        <Input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search conversations..."
          className="w-full"
        />
      </div>

      {/* Count + Select toggle */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">
            {total} chat{total !== 1 ? "s" : ""}
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
              {selectedIds.size === sorted.length ? "Deselect all" : "Select all"}
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
              Delete ({selectedIds.size})
            </Button>
          )}
          {selectMode ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={exitSelectMode}
            >
              Cancel
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSelectMode(true)}
            >
              Select
            </Button>
          )}
        </div>
      </div>

      {/* Conversation list */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : sorted.length === 0 ? (
        <div className="py-16 text-center text-sm text-muted-foreground">
          {debouncedQuery ? "No results found" : "No conversations yet"}
        </div>
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
                  onChange={() => handleSelect(conv.id)}
                  onClick={(e) => e.stopPropagation()}
                  className="h-4 w-4 shrink-0 rounded border-border accent-primary"
                />
              )}
              {conv.starred && (
                <Star className="h-4 w-4 shrink-0 fill-current text-yellow-500" />
              )}
              <span className="flex-1 truncate text-sm">
                {conv.title || "Untitled"}
              </span>
              <span className="text-xs text-muted-foreground shrink-0">
                {formatRelativeTime(conv.created_at)}
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
                      Select
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation()
                      handleStarToggle(e as unknown as React.MouseEvent, conv)
                    }}>
                      <Star className={cn("h-4 w-4 mr-2", conv.starred && "fill-current text-yellow-500")} />
                      {conv.starred ? "Unstar" : "Star"}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={(e) => {
                      e.stopPropagation()
                      setRenamingId(conv.id)
                      setRenameValue(conv.title || "")
                      setRenameDialogOpen(true)
                    }}>
                      <Pencil className="h-4 w-4 mr-2" />
                      Rename
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onClick={(e) => {
                        e.stopPropagation()
                        setSingleDeleteId(conv.id)
                        setDeleteDialogOpen(true)
                      }}
                    >
                      <Trash2 className="h-4 w-4 mr-2" />
                      Delete
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
                Loading...
              </>
            ) : (
              "Show more"
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
                ? "Delete this conversation?"
                : `Delete ${selectedIds.size} conversation${selectedIds.size !== 1 ? "s" : ""}?`}
            </DialogTitle>
            <DialogDescription>
              This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => { setDeleteDialogOpen(false); setSingleDeleteId(null) }}>
              Cancel
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
                  Deleting...
                </>
              ) : (
                "Delete"
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
              Rename conversation
            </DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleRenameSubmit() }}
            placeholder="Conversation title"
          />
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => { setRenameDialogOpen(false); setRenamingId(null) }}>
              Cancel
            </Button>
            <Button className="px-6" onClick={handleRenameSubmit} disabled={!renameValue.trim()}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
    </div>
  )
}

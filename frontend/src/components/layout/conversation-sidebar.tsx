"use client"

import { useState, useCallback } from "react"
import { useRouter, usePathname } from "next/navigation"
import { Plus, Trash2, MessageSquare, Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useConversation } from "@/contexts/conversation-context"
import type { ConversationResponse } from "@/types/conversation"

interface ConversationSidebarProps {
  collapsed: boolean
}

function groupByDate(conversations: ConversationResponse[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)
  const weekAgo = new Date(today.getTime() - 7 * 86400000)

  const groups: { label: string; items: ConversationResponse[] }[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 Days", items: [] },
    { label: "Older", items: [] },
  ]

  for (const conv of conversations) {
    const d = new Date(conv.created_at)
    if (d >= today) groups[0].items.push(conv)
    else if (d >= yesterday) groups[1].items.push(conv)
    else if (d >= weekAgo) groups[2].items.push(conv)
    else groups[3].items.push(conv)
  }

  return groups.filter((g) => g.items.length > 0)
}

export function ConversationSidebar({ collapsed }: ConversationSidebarProps) {
  const {
    conversations,
    activeId,
    isLoadingList,
    selectConversation,
    clearActive,
    deleteConversation,
  } = useConversation()
  const router = useRouter()
  const pathname = usePathname()
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)

  const handleSelectConversation = useCallback((id: string) => {
    selectConversation(id)
    if (pathname !== "/") router.push(`/?c=${id}`)
  }, [selectConversation, pathname, router])

  const handleNewChat = useCallback(() => {
    clearActive()
    if (pathname !== "/") router.push("/")
  }, [clearActive, pathname, router])

  const handleDeleteClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setPendingDeleteId(id)
  }

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    setDeletingId(id)
    try {
      await deleteConversation(id)
    } finally {
      setDeletingId(null)
    }
  }

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 py-2">
        <button
          onClick={handleNewChat}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          title="New Chat"
        >
          <Plus className="h-4 w-4" />
        </button>
      </div>
    )
  }

  const groups = groupByDate(conversations)

  return (
    <div className="flex flex-col h-full">
      {/* New Chat button */}
      <div className="px-2 pb-2">
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start gap-2"
          onClick={handleNewChat}
        >
          <Plus className="h-4 w-4" />
          New Chat
        </Button>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="px-2 pb-2">
          {isLoadingList ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : conversations.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted-foreground">
              No conversations yet
            </div>
          ) : (
            groups.map((group) => (
              <div key={group.label} className="mb-3">
                <div className="px-2 py-1.5 text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">
                  {group.label}
                </div>
                {group.items.map((conv) => (
                  <div
                    key={conv.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleSelectConversation(conv.id)}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleSelectConversation(conv.id) }}
                    className={cn(
                      "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors text-left cursor-pointer",
                      activeId === conv.id
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                    )}
                  >
                    <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-50" />
                    <span className="flex-1 truncate text-[13px]">
                      {conv.title || "Untitled"}
                    </span>
                    <Badge
                      variant="secondary"
                      className="shrink-0 text-[10px] px-1 py-0 h-4 opacity-60"
                    >
                      {conv.mode === "react" ? "ReAct" : conv.mode === "dag" ? "DAG" : conv.mode}
                    </Badge>
                    <button
                      onClick={(e) => handleDeleteClick(e, conv.id)}
                      disabled={deletingId === conv.id}
                      className="shrink-0 opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:text-destructive transition-opacity"
                    >
                      {deletingId === conv.id ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Trash2 className="h-3 w-3" />
                      )}
                    </button>
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
      </ScrollArea>

      {/* Delete confirmation dialog */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete conversation?</DialogTitle>
            <DialogDescription>
              This conversation will be permanently deleted. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>
              Cancel
            </Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

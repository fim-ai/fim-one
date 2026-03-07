"use client"

import { useState, useCallback, useEffect } from "react"
import { useTranslations } from "next-intl"
import { Plus, Trash2, Loader2, Search, Star, MoreHorizontal, Pencil, MessagesSquare, GitBranch, Zap } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useConversation } from "@/contexts/conversation-context"
import { ChatSearchDialog } from "@/components/layout/chat-search-dialog"
import type { ConversationResponse } from "@/types/conversation"

interface ConversationSidebarProps {
  collapsed: boolean
  hideHeader?: boolean
}

function groupByDate(
  conversations: ConversationResponse[],
  labels: { starred: string; today: string; yesterday: string; previous7Days: string; older: string },
) {
  const starred = conversations.filter((c) => c.starred)
  const unstarred = conversations.filter((c) => !c.starred)

  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)
  const weekAgo = new Date(today.getTime() - 7 * 86400000)

  const groups: { label: string; items: ConversationResponse[] }[] = []

  if (starred.length > 0) {
    groups.push({ label: labels.starred, items: starred })
  }

  const dateGroups: { label: string; items: ConversationResponse[] }[] = [
    { label: labels.today, items: [] },
    { label: labels.yesterday, items: [] },
    { label: labels.previous7Days, items: [] },
    { label: labels.older, items: [] },
  ]

  for (const conv of unstarred) {
    const d = new Date(conv.created_at)
    if (d >= today) dateGroups[0].items.push(conv)
    else if (d >= yesterday) dateGroups[1].items.push(conv)
    else if (d >= weekAgo) dateGroups[2].items.push(conv)
    else dateGroups[3].items.push(conv)
  }

  for (const g of dateGroups) {
    if (g.items.length > 0) groups.push(g)
  }

  return groups
}

export function ConversationSidebar({ collapsed, hideHeader }: ConversationSidebarProps) {
  const pathname = usePathname()
  const isNewChat = pathname === "/new"
  const t = useTranslations("layout")
  const tc = useTranslations("common")
  const {
    conversations,
    activeId,
    isLoadingList,
    selectConversation,
    clearActive,
    deleteConversation,
    updateTitle,
    typingTitles,
    toggleStar,
  } = useConversation()
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<{ id: string; title: string } | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [searchOpen, setSearchOpen] = useState(false)

  // Global Cmd+K / Ctrl+K shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault()
        setSearchOpen(true)
      }
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [])

  const handleNewChat = useCallback(() => {
    clearActive()
  }, [clearActive])

  const confirmDelete = async () => {
    if (!pendingDeleteId) return
    const id = pendingDeleteId
    setPendingDeleteId(null)
    await deleteConversation(id)
  }

  const openRename = (conv: ConversationResponse) => {
    setRenameValue(conv.title || "")
    setRenameTarget({ id: conv.id, title: conv.title || "" })
  }

  const confirmRename = async () => {
    if (!renameTarget || !renameValue.trim()) return
    await updateTitle(renameTarget.id, renameValue.trim())
    setRenameTarget(null)
  }

  if (collapsed) {
    if (hideHeader) return null
    return (
      <div className="flex flex-col items-center gap-2 py-2">
        <Link
          href="/new"
          onClick={handleNewChat}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-md transition-colors",
            isNewChat
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          )}
          title={t("newChat")}
        >
          <Plus className="h-4 w-4" />
        </Link>
        <button
          onClick={() => setSearchOpen(true)}
          className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors"
          title={tc("search")}
        >
          <Search className="h-4 w-4" />
        </button>
        <ChatSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
      </div>
    )
  }

  const groups = groupByDate(conversations, {
    starred: t("starred"),
    today: tc("today"),
    yesterday: tc("yesterday"),
    previous7Days: t("previous7Days"),
    older: tc("older"),
  })

  return (
    <div className="flex flex-col h-full">
      {/* New Chat + Search row */}
      {!hideHeader && (
        <div className="flex items-center gap-1.5 px-2 pb-2">
          <Button
            variant={isNewChat ? "default" : "outline"}
            size="sm"
            className="flex-1 justify-start gap-2"
            asChild
          >
            <Link href="/new" onClick={handleNewChat}>
              <Plus className="h-4 w-4" />
              {t("newChat")}
            </Link>
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0 px-2"
            onClick={() => setSearchOpen(true)}
            title={t("searchTooltipMac", { shortcut: "⌘K" })}
          >
            <Search className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Conversation list + All chats (scrollable) */}
      <div className="flex-1 min-h-0">
        <ScrollArea className="h-full">
          <div className="px-2 pb-2">
            {isLoadingList ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : conversations.length === 0 ? (
              <div className="py-8 text-center text-xs text-muted-foreground">
                {t("noConversations")}
              </div>
            ) : (
              <>
                {groups.map((group) => (
                  <div key={group.label} className="mb-3">
                    <div className="px-2 py-1.5 text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">
                      {group.label}
                    </div>
                    {group.items.map((conv) => (
                      <Link
                        key={conv.id}
                        href={`/?c=${conv.id}`}
                        onClick={() => { selectConversation(conv.id) }}
                        className={cn(
                          "group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors text-left cursor-pointer",
                          activeId === conv.id && pathname === "/"
                            ? "bg-accent/60 text-accent-foreground"
                            : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground",
                        )}
                      >
                        <span className="flex-1 truncate text-[13px]">
                          {conv.id in typingTitles ? typingTitles[conv.id] : (conv.title || t("untitled"))}
                        </span>
                        {conv.mode === "dag" ? (
                          <GitBranch className="shrink-0 h-3 w-3 text-muted-foreground/40 opacity-0 group-hover:opacity-100 transition-opacity" />
                        ) : (
                          <Zap className="shrink-0 h-3 w-3 text-muted-foreground/40 opacity-0 group-hover:opacity-100 transition-opacity" />
                        )}
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              onClick={(e) => e.stopPropagation()}
                              className="shrink-0 opacity-0 group-hover:opacity-70 hover:!opacity-100 transition-opacity rounded p-0.5 hover:bg-accent"
                            >
                              <MoreHorizontal className="h-3.5 w-3.5" />
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-36">
                            <DropdownMenuItem onClick={(e) => { e.stopPropagation(); openRename(conv) }}>
                              <Pencil className="h-3.5 w-3.5 mr-2" />
                              {t("rename")}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={(e) => { e.stopPropagation(); toggleStar(conv.id) }}>
                              <Star className={cn("h-3.5 w-3.5 mr-2", conv.starred && "fill-yellow-500 text-yellow-500")} />
                              {conv.starred ? t("unstar") : t("star")}
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              variant="destructive"
                              onClick={(e) => { e.stopPropagation(); setPendingDeleteId(conv.id) }}
                            >
                              <Trash2 className="h-3.5 w-3.5 mr-2" />
                              {tc("delete")}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </Link>
                    ))}
                  </div>
                ))}
                {/* Bottom "All Chats" link */}
                <Link
                  href="/chats"
                  className="flex items-center gap-2 rounded-md px-2 py-1.5 mt-1 text-xs text-muted-foreground/60 hover:text-muted-foreground hover:bg-accent/50 transition-colors"
                >
                  <MessagesSquare className="h-3.5 w-3.5" />
                  {t("allChats")}
                </Link>
              </>
            )}
          </div>
        </ScrollArea>
      </div>

      {/* Search dialog */}
      <ChatSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />

      {/* Delete confirmation dialog */}
      <Dialog open={pendingDeleteId !== null} onOpenChange={(open) => { if (!open) setPendingDeleteId(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteConversationTitle")}</DialogTitle>
            <DialogDescription>
              {t("deleteConversationDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setPendingDeleteId(null)}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" className="px-6" onClick={confirmDelete}>
              {tc("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {/* Rename dialog */}
      <Dialog open={renameTarget !== null} onOpenChange={(open) => { if (!open) setRenameTarget(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("renameConversationTitle")}</DialogTitle>
            <DialogDescription>
              {t("renameConversationDescription")}
            </DialogDescription>
          </DialogHeader>
          <Input
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") confirmRename() }}
            placeholder={t("conversationTitlePlaceholder")}
            autoFocus
          />
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setRenameTarget(null)}>
              {tc("cancel")}
            </Button>
            <Button className="px-6" onClick={confirmRename} disabled={!renameValue.trim()}>
              {tc("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

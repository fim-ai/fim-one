"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { Search, Loader2, MessageSquare } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { conversationApi } from "@/lib/api"
import { useConversation } from "@/contexts/conversation-context"
import type { ConversationResponse } from "@/types/conversation"

interface ChatSearchDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function formatRelativeTime(
  dateStr: string,
  t: (key: string, values?: Record<string, number>) => string,
): string {
  const now = Date.now()
  const d = new Date(dateStr).getTime()
  const diff = now - d
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return t("justNow")
  if (mins < 60) return t("minutesAgo", { minutes: mins })
  const hours = Math.floor(mins / 60)
  if (hours < 24) return t("hoursAgo", { hours })
  const days = Math.floor(hours / 24)
  if (days < 30) return t("daysAgo", { days })
  const months = Math.floor(days / 30)
  return t("monthsAgo", { months })
}

function groupResultsByTime(
  results: ConversationResponse[],
  labels: { today: string; pastWeek: string; pastMonth: string; older: string },
) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const weekAgo = new Date(today.getTime() - 7 * 86400000)
  const monthAgo = new Date(today.getTime() - 30 * 86400000)

  const groups: { label: string; items: ConversationResponse[] }[] = [
    { label: labels.today, items: [] },
    { label: labels.pastWeek, items: [] },
    { label: labels.pastMonth, items: [] },
    { label: labels.older, items: [] },
  ]

  for (const conv of results) {
    const d = new Date(conv.created_at)
    if (d >= today) groups[0].items.push(conv)
    else if (d >= weekAgo) groups[1].items.push(conv)
    else if (d >= monthAgo) groups[2].items.push(conv)
    else groups[3].items.push(conv)
  }

  return groups.filter((g) => g.items.length > 0)
}

export function ChatSearchDialog({ open, onOpenChange }: ChatSearchDialogProps) {
  const router = useRouter()
  const t = useTranslations("layout")
  const tc = useTranslations("common")
  const { selectConversation } = useConversation()
  const [query, setQuery] = useState("")
  const [debouncedQuery, setDebouncedQuery] = useState("")
  const [results, setResults] = useState<ConversationResponse[]>([])
  const [loading, setLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(timer)
  }, [query])

  // Fetch results when debounced query changes
  useEffect(() => {
    if (!open) return
    if (!debouncedQuery.trim()) {
      setResults([])
      return
    }

    let cancelled = false
    setLoading(true)
    conversationApi.list(1, 20, debouncedQuery).then((res) => {
      if (!cancelled) {
        setResults(res.items)
        setLoading(false)
      }
    }).catch(() => {
      if (!cancelled) setLoading(false)
    })

    return () => { cancelled = true }
  }, [debouncedQuery, open])

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setQuery("")
      setDebouncedQuery("")
      setResults([])
    }
  }, [open])

  // Focus input when dialog opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 0)
    }
  }, [open])

  const handleSelect = useCallback((id: string) => {
    onOpenChange(false)
    selectConversation(id)
    router.push(`/?c=${id}`)
  }, [onOpenChange, selectConversation, router])

  const groups = groupResultsByTime(results, {
    today: tc("today"),
    pastWeek: t("pastWeek"),
    pastMonth: t("pastMonth"),
    older: tc("older"),
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl p-0 gap-0 overflow-hidden">
        <DialogHeader className="sr-only">
          <DialogTitle>{t("searchConversations")}</DialogTitle>
        </DialogHeader>
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchConversationsPlaceholder")}
            className="border-0 shadow-none focus-visible:ring-0 px-0 h-8"
          />
        </div>

        <ScrollArea className="max-h-[400px]">
          <div className="p-2">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            ) : !debouncedQuery.trim() ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {t("typeToSearch")}
              </div>
            ) : groups.length === 0 ? (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {tc("noResults")}
              </div>
            ) : (
              groups.map((group) => (
                <div key={group.label} className="mb-2">
                  <div className="px-2 py-1 text-[11px] font-medium text-muted-foreground/70 uppercase tracking-wider">
                    {group.label}
                  </div>
                  {group.items.map((conv) => (
                    <button
                      key={conv.id}
                      onClick={() => handleSelect(conv.id)}
                      className="flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-left hover:bg-accent transition-colors"
                    >
                      <MessageSquare className="h-3.5 w-3.5 shrink-0 opacity-50" />
                      <span className="flex-1 truncate">
                        {conv.title || t("untitled")}
                      </span>
                      <span className="text-xs text-muted-foreground shrink-0">
                        {formatRelativeTime(conv.created_at, t)}
                      </span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

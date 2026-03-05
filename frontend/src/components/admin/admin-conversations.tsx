"use client"

import { useState, useEffect, useCallback, useRef } from "react"
import { Trash2, Search, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { adminApi } from "@/lib/api"
import type { AdminConversation } from "@/types/admin"

const PAGE_SIZE = 20

export function AdminConversations() {
  const [conversations, setConversations] = useState<AdminConversation[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState("")
  const [isLoading, setIsLoading] = useState(true)
  const [deleteTarget, setDeleteTarget] = useState<AdminConversation | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const errMsg = (err: unknown) =>
    err instanceof Error ? err.message : "Operation failed"

  const loadConversations = useCallback(async () => {
    setIsLoading(true)
    try {
      const res = await adminApi.listAllConversations({ page, size: PAGE_SIZE, q: search || undefined })
      setConversations(res.items)
      setTotal(res.total)
    } catch (err) {
      toast.error(errMsg(err))
    } finally {
      setIsLoading(false)
    }
  }, [page, search])

  useEffect(() => { loadConversations() }, [loadConversations])

  const handleSearchChange = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      setSearch(value)
      setPage(1)
    }, 300)
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    try {
      await adminApi.adminDeleteConversation(deleteTarget.id)
      toast.success("Conversation deleted")
      setDeleteTarget(null)
      loadConversations()
    } catch (err) {
      toast.error(errMsg(err))
    }
  }

  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-4">
      {/* Page header */}
      <div>
        <h2 className="text-base font-semibold">Conversations</h2>
        <p className="text-sm text-muted-foreground">Browse and moderate all user conversations.</p>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by user or title..."
            className="pl-9"
            onChange={(e) => handleSearchChange(e.target.value)}
          />
        </div>
        <span className="text-sm text-muted-foreground shrink-0">{total} total</span>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : conversations.length === 0 ? (
        <div className="rounded-md border border-border bg-muted/30 p-4 text-sm text-muted-foreground">
          No conversations found.
        </div>
      ) : (
        <div className="rounded-md border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/40">
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">User</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Title</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Mode</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Model</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Tokens</th>
                <th className="px-4 py-2.5 text-right font-medium text-muted-foreground">Msgs</th>
                <th className="px-4 py-2.5 text-left font-medium text-muted-foreground">Date</th>
                <th className="px-4 py-2.5 w-10" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {conversations.map((conv) => (
                <tr key={conv.id} className="hover:bg-muted/20 transition-colors">
                  <td className="px-4 py-3 font-medium text-foreground">{conv.username}</td>
                  <td className="px-4 py-3 max-w-[200px] truncate text-muted-foreground">
                    {conv.title ?? "Untitled"}
                  </td>
                  <td className="px-4 py-3">
                    {conv.mode ? <Badge variant="outline">{conv.mode}</Badge> : <span className="text-muted-foreground/50">--</span>}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">{conv.model_name ?? "--"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{conv.total_tokens.toLocaleString()}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{conv.message_count}</td>
                  <td className="px-4 py-3 text-muted-foreground text-xs">
                    {new Date(conv.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 w-7 p-0 text-destructive"
                      onClick={() => setDeleteTarget(conv)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!isLoading && conversations.length > 0 && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{total} conversation{total !== 1 ? "s" : ""} total</span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              Previous
            </Button>
            <span>
              Page {page} of {pages}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= pages}
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
            >
              Next
            </Button>
          </div>
        </div>
      )}

      {/* Delete AlertDialog */}
      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &quot;{deleteTarget?.title ?? "Untitled"}&quot; by {deleteTarget?.username}. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} className="bg-destructive text-destructive-foreground hover:bg-destructive/90">
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

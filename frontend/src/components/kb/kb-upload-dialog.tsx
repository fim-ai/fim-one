"use client"

import { useState, useRef } from "react"
import { Loader2, Upload, CheckCircle2, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog"
import { kbApi } from "@/lib/api"
import type { KBResponse } from "@/types/kb"

interface KBUploadDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kb: KBResponse | null
  onUploaded: () => void
}

interface UploadItem {
  file: File
  status: "pending" | "uploading" | "done" | "error"
  error?: string
}

const ACCEPTED_TYPES = ".pdf,.docx,.md,.html,.csv,.txt"

export function KBUploadDialog({
  open,
  onOpenChange,
  kb,
  onUploaded,
}: KBUploadDialogProps) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    const newItems: UploadItem[] = Array.from(files).map((file) => ({
      file,
      status: "pending" as const,
    }))
    setItems((prev) => [...prev, ...newItems])
    // Reset input so the same file can be selected again
    if (fileInputRef.current) fileInputRef.current.value = ""
  }

  const handleUpload = async () => {
    if (!kb || items.length === 0) return
    setIsUploading(true)

    for (let i = 0; i < items.length; i++) {
      if (items[i].status !== "pending") continue

      setItems((prev) =>
        prev.map((item, idx) =>
          idx === i ? { ...item, status: "uploading" } : item
        )
      )

      try {
        await kbApi.uploadDocument(kb.id, items[i].file)
        setItems((prev) =>
          prev.map((item, idx) =>
            idx === i ? { ...item, status: "done" } : item
          )
        )
      } catch (err) {
        const message = err instanceof Error ? err.message : "Upload failed"
        setItems((prev) =>
          prev.map((item, idx) =>
            idx === i ? { ...item, status: "error", error: message } : item
          )
        )
      }
    }

    setIsUploading(false)
    onUploaded()
  }

  const handleClose = (openState: boolean) => {
    if (!openState) {
      setItems([])
      setIsUploading(false)
    }
    onOpenChange(openState)
  }

  const pendingCount = items.filter((i) => i.status === "pending").length

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload Documents{kb ? ` to ${kb.name}` : ""}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* File input */}
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED_TYPES}
              multiple
              onChange={handleFileSelect}
              className="flex-1 text-sm file:mr-2 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-primary-foreground hover:file:bg-primary/90 cursor-pointer"
            />
          </div>

          {/* File list */}
          {items.length > 0 && (
            <div className="max-h-60 overflow-y-auto space-y-2">
              {items.map((item, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 text-sm rounded-md border border-border px-3 py-2"
                >
                  <span className="flex-1 truncate">{item.file.name}</span>
                  {item.status === "pending" && (
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-5">
                      Pending
                    </Badge>
                  )}
                  {item.status === "uploading" && (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground shrink-0" />
                  )}
                  {item.status === "done" && (
                    <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
                  )}
                  {item.status === "error" && (
                    <span title={item.error}>
                      <XCircle className="h-4 w-4 text-destructive shrink-0" />
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => handleClose(false)}
            disabled={isUploading}
          >
            {isUploading ? "Close" : "Cancel"}
          </Button>
          <Button
            onClick={handleUpload}
            disabled={isUploading || pendingCount === 0}
            className="gap-1.5"
          >
            {isUploading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Upload className="h-4 w-4" />
            )}
            Upload {pendingCount > 0 ? `(${pendingCount})` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

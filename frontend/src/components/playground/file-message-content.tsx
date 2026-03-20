"use client"

import { Download, Paperclip } from "lucide-react"
import { formatFileSize } from "@/lib/utils"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"

export interface FileMetadataItem {
  file_id: string
  filename: string
  size: number
  mime_type?: string | null
  content_preview?: string | null
}

export interface FileMessageMetadata {
  files: FileMetadataItem[]
  userQuery: string
}

interface FileMessageContentProps {
  metadata: FileMessageMetadata
}

function downloadFile(fileId: string, filename: string) {
  const token = localStorage.getItem(ACCESS_TOKEN_KEY)
  fetch(`${getApiBaseUrl()}/api/files/${fileId}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
    .then((res) => {
      if (!res.ok) throw new Error("Download failed")
      return res.blob()
    })
    .then((blob) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    })
    .catch(() => {})
}

/**
 * Renders read-only file cards for a user message that had file attachments.
 * Used in history messages and pending (in-flight) messages after page refresh.
 * Displays file cards + the original user query text (without injected file content).
 */
export function FileMessageContent({ metadata }: FileMessageContentProps) {
  return (
    <div className="space-y-2">
      {/* File cards */}
      <div className="flex flex-wrap gap-2">
        {metadata.files.map((file) => (
          <button
            key={file.file_id}
            onClick={() => downloadFile(file.file_id, file.filename)}
            className="flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-1 text-xs hover:bg-muted/60 transition-colors cursor-pointer"
          >
            <Paperclip className="h-3 w-3 text-muted-foreground" />
            <span className="max-w-[150px] truncate">{file.filename}</span>
            <span className="text-muted-foreground">({formatFileSize(file.size)})</span>
            <Download className="h-3 w-3 text-muted-foreground" />
          </button>
        ))}
      </div>

      {/* User query text */}
      {metadata.userQuery && (
        <p className="text-sm text-foreground whitespace-pre-wrap">{metadata.userQuery}</p>
      )}
    </div>
  )
}

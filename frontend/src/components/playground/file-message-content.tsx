"use client"

import { Paperclip } from "lucide-react"
import { formatFileSize } from "@/lib/utils"

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
          <div
            key={file.file_id}
            className="flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-1 text-xs"
          >
            <Paperclip className="h-3 w-3 text-muted-foreground" />
            <span className="max-w-[150px] truncate">{file.filename}</span>
            <span className="text-muted-foreground">({formatFileSize(file.size)})</span>
          </div>
        ))}
      </div>

      {/* User query text */}
      {metadata.userQuery && (
        <p className="text-sm text-foreground whitespace-pre-wrap">{metadata.userQuery}</p>
      )}
    </div>
  )
}

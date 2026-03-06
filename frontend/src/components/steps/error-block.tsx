"use client"

import { useTranslations } from "next-intl"
import { AlertCircle } from "lucide-react"

interface ErrorBlockProps {
  error: string
  size?: "default" | "compact"
}

export function ErrorBlock({ error, size = "default" }: ErrorBlockProps) {
  const tc = useTranslations("common")
  const isCompact = size === "compact"
  return (
    <div className={`rounded border border-destructive/30 bg-destructive/5 ${isCompact ? "p-2" : "p-3"}`}>
      <div className={`flex items-center gap-1 ${isCompact ? "mb-0.5" : "mb-1"}`}>
        <AlertCircle className={`${isCompact ? "h-2.5 w-2.5" : "h-3 w-3"} text-destructive`} />
        <p className={`font-medium text-destructive uppercase tracking-wider ${isCompact ? "text-[10px]" : "text-xs"}`}>
          {tc("error")}
        </p>
      </div>
      <pre className={`whitespace-pre-wrap text-destructive/90 font-mono ${isCompact ? "text-xs" : "text-xs"}`}>
        {error}
      </pre>
    </div>
  )
}

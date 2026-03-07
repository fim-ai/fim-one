"use client"

import { useTranslations } from "next-intl"
import { MessageCircleQuestion } from "lucide-react"

interface SuggestedFollowupsProps {
  suggestions: string[]
  onSelect: (query: string) => void
}

export function SuggestedFollowups({ suggestions, onSelect }: SuggestedFollowupsProps) {
  const t = useTranslations("playground")
  if (!suggestions.length) return null

  return (
    <div className="mt-4 pt-4 border-t">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
        <MessageCircleQuestion className="h-3.5 w-3.5" />
        <span>{t("followUp")}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {suggestions.map((suggestion, i) => (
          <button
            key={i}
            onClick={() => onSelect(suggestion)}
            className="text-sm px-3 py-1.5 rounded-full border bg-muted/30 hover:bg-primary/10 hover:border-primary/30 transition-colors text-left"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  )
}

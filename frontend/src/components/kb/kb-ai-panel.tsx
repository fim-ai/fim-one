"use client"

import { AIPanel } from "@/components/shared/ai-panel"

interface KBAIPanelProps {
  kbId: string | null
  onKbChanged?: () => void
}

export function KBAIPanel({ kbId, onKbChanged }: KBAIPanelProps) {
  return (
    <AIPanel
      mode="kb"
      id={kbId}
      onKbChanged={onKbChanged}
    />
  )
}

"use client"

interface ObservationBlockProps {
  observation: string
  size?: "default" | "compact"
  hideLabel?: boolean
}

export function ObservationBlock({
  observation,
  size = "default",
  hideLabel = false,
}: ObservationBlockProps) {
  const isCompact = size === "compact"
  return (
    <div className={`rounded${isCompact ? "" : "-md"} border border-border/30 ${isCompact ? "bg-muted/30 p-2" : "border-border/50 bg-muted/30 p-3"}`}>
      {!hideLabel && (
        <p className={`font-medium text-muted-foreground ${isCompact ? "text-[10px] mb-0.5" : "text-xs mb-1"} uppercase tracking-wider`}>
          Observation
        </p>
      )}
      <pre className="whitespace-pre-wrap break-all text-xs text-foreground/90 font-mono leading-relaxed overflow-x-auto">
        {observation}
      </pre>
    </div>
  )
}

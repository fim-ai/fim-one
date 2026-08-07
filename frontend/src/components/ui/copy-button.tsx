"use client"

import * as React from "react"
import { Check, Copy } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"

interface CopyButtonProps {
  /** Text to copy. Pass a function when building the text is expensive. */
  text: string | (() => string)
  /** Visible label. Omit for an icon-only button. */
  label?: string
  /** Accessible name; falls back to the label, then to the generic "Copy". */
  title?: string
  className?: string
  iconClassName?: string
}

/** Copy-to-clipboard button that swaps to a checkmark for 1.5s after copying. */
export function CopyButton({ text, label, title, className, iconClassName }: CopyButtonProps) {
  const [copied, setCopied] = React.useState(false)
  const t = useTranslations("common")
  const timer = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  React.useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])

  const handleCopy = React.useCallback(() => {
    const value = typeof text === "function" ? text() : text
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    })
  }, [text])

  const accessibleName = title ?? label ?? t("copy")
  const Icon = copied ? Check : Copy

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={accessibleName}
      aria-label={accessibleName}
      className={cn(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-muted-foreground",
        "transition-colors hover:bg-muted hover:text-foreground",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        className,
      )}
    >
      <Icon className={cn("h-3.5 w-3.5", copied && "text-green-500", iconClassName)} />
      {label && <span>{copied ? t("copied") : label}</span>}
    </button>
  )
}

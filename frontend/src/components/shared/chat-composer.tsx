"use client"

import * as React from "react"
import { Loader2, Send, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

/**
 * Shared chat-composer primitives: the pill container, the borderless
 * auto-growing textarea, and the round send/stop button.
 *
 * The playground composes these with its own attachments, slash commands,
 * and pickers; the side AI panels use them bare. Keep visual decisions here
 * so every chat surface reads as one product.
 */

export function ComposerShell({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="composer-shell"
      className={cn(
        "relative rounded-[26px] border border-border/60 bg-background/95 shadow-lg shadow-black/5 backdrop-blur-md",
        "transition-[border-color,box-shadow] duration-200 focus-within:border-ring/50 focus-within:shadow-xl focus-within:shadow-black/10",
        className
      )}
      {...props}
    />
  )
}

/** The bottom row inside the shell: controls left and right of the textarea. */
export function ComposerRow({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="composer-row"
      className={cn("relative flex items-end gap-1 px-2.5 py-2", className)}
      {...props}
    />
  )
}

/** Single-line at rest, grows with content, capped so the message stream stays visible. */
export function ComposerTextarea({ className, ...props }: React.ComponentProps<typeof Textarea>) {
  return (
    <Textarea
      rows={1}
      className={cn(
        "min-h-9 max-h-[200px] flex-1 resize-none self-center border-0 bg-transparent px-2 py-2 shadow-none",
        "dark:bg-transparent hover:border-transparent focus-visible:outline-none focus-visible:border-transparent",
        className
      )}
      {...props}
    />
  )
}

export type ComposerSendState = "send" | "stop" | "busy"

interface ComposerSendButtonProps
  extends Omit<React.ComponentProps<typeof Button>, "children" | "variant" | "size" | "aria-label"> {
  /** `busy` shows a spinner (e.g. a send queued behind an upload); `stop` turns the button destructive. */
  state?: ComposerSendState
  sendLabel: string
  stopLabel?: string
}

export function ComposerSendButton({
  state = "send",
  sendLabel,
  stopLabel,
  className,
  ...props
}: ComposerSendButtonProps) {
  const isStop = state === "stop"
  return (
    <Button
      size="icon"
      variant={isStop ? "destructive" : "default"}
      aria-label={isStop ? (stopLabel ?? sendLabel) : sendLabel}
      className={cn("h-9 w-9 shrink-0 rounded-full", className)}
      {...props}
    >
      {state === "busy" ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : isStop ? (
        <Square className="h-3.5 w-3.5" />
      ) : (
        <Send className="h-4 w-4" />
      )}
    </Button>
  )
}

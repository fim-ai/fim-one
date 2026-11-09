"use client"

import { useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import {
  Beaker,
  Clipboard,
  Copy,
  LayoutGrid,
  Maximize2,
  MousePointerSquareDashed,
  Settings,
  Trash2,
} from "lucide-react"

export type CanvasContextMenu =
  | { type: "node"; x: number; y: number; nodeId: string }
  | { type: "pane"; x: number; y: number }
  | null

/** Which context menu is open; it closes on any click or scroll. */
export function useCanvasContextMenu() {
  const [contextMenu, setContextMenu] = useState<CanvasContextMenu>(null)

  // Close context menu on scroll or click outside
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    window.addEventListener("click", close)
    window.addEventListener("scroll", close, true)
    return () => {
      window.removeEventListener("click", close)
      window.removeEventListener("scroll", close, true)
    }
  }, [contextMenu])

  return [contextMenu, setContextMenu] as const
}

const MENU_CLASS =
  "fixed z-50 min-w-[160px] rounded-md border border-border bg-popover p-1 shadow-md animate-in fade-in-0 zoom-in-95"
const ITEM_CLASS =
  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"

interface NodeContextMenuProps {
  x: number
  y: number
  /** Start and End cannot be deleted. */
  canDelete: boolean
  onConfigure: () => void
  onTest: () => void
  onCopy: () => void
  onDuplicate: () => void
  onDelete: () => void
}

/** Right-click menu of a node. Every entry closes the menu itself. */
export function NodeContextMenu({
  x,
  y,
  canDelete,
  onConfigure,
  onTest,
  onCopy,
  onDuplicate,
  onDelete,
}: NodeContextMenuProps) {
  const t = useTranslations("workflows")
  return (
    <div className={MENU_CLASS} style={{ left: x, top: y }} onClick={(e) => e.stopPropagation()}>
      <button className={ITEM_CLASS} onClick={onConfigure}>
        <Settings className="h-3.5 w-3.5" />
        {t("contextMenuConfigure")}
      </button>
      <button className={ITEM_CLASS} onClick={onTest}>
        <Beaker className="h-3.5 w-3.5" />
        {t("contextMenuTestNode")}
      </button>
      <button className={ITEM_CLASS} onClick={onCopy}>
        <Copy className="h-3.5 w-3.5" />
        {t("contextMenuCopy")}
      </button>
      <button className={ITEM_CLASS} onClick={onDuplicate}>
        <Clipboard className="h-3.5 w-3.5" />
        {t("contextMenuDuplicate")}
      </button>
      {canDelete && (
        <>
          <div className="my-1 h-px bg-border" />
          <button
            className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive hover:bg-destructive/10 transition-colors"
            onClick={onDelete}
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("contextMenuDelete")}
          </button>
        </>
      )}
    </div>
  )
}

interface PaneContextMenuProps {
  x: number
  y: number
  canPaste: boolean
  onPaste: () => void
  onSelectAll: () => void
  onFitView: () => void
  onAutoLayout: () => void
}

/** Right-click menu of the empty canvas. */
export function PaneContextMenu({
  x,
  y,
  canPaste,
  onPaste,
  onSelectAll,
  onFitView,
  onAutoLayout,
}: PaneContextMenuProps) {
  const t = useTranslations("workflows")
  return (
    <div className={MENU_CLASS} style={{ left: x, top: y }} onClick={(e) => e.stopPropagation()}>
      <button
        disabled={!canPaste}
        className={`${ITEM_CLASS} disabled:opacity-40 disabled:cursor-not-allowed`}
        onClick={onPaste}
      >
        <Clipboard className="h-3.5 w-3.5" />
        {t("contextMenuPaste")}
      </button>
      <button className={ITEM_CLASS} onClick={onSelectAll}>
        <MousePointerSquareDashed className="h-3.5 w-3.5" />
        {t("contextMenuSelectAll")}
      </button>
      <div className="my-1 h-px bg-border" />
      <button className={ITEM_CLASS} onClick={onFitView}>
        <Maximize2 className="h-3.5 w-3.5" />
        {t("contextMenuFitView")}
      </button>
      <button className={ITEM_CLASS} onClick={onAutoLayout}>
        <LayoutGrid className="h-3.5 w-3.5" />
        {t("contextMenuAutoLayout")}
      </button>
    </div>
  )
}

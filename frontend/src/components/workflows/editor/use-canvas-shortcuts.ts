import { useEffect } from "react"

interface CanvasShortcutHandlers {
  undo: () => void
  redo: () => void
  deleteSelected: () => void
  copySelected: () => void
  paste: () => void
  selectAll: () => void
  duplicateSelected: () => void
  showShortcuts: () => void
  /** Whether the find bar is open: Escape closes it before anything else. */
  searchOpen: boolean
  openSearch: () => void
  closeSearch: () => void
  /** Escape with no find bar: deselect everything and close the settings panel. */
  clearSelection: () => void
}

/** Keyboard shortcuts of the canvas, bound on the window while the editor is mounted. */
export function useCanvasShortcuts({
  undo,
  redo,
  deleteSelected,
  copySelected,
  paste,
  selectAll,
  duplicateSelected,
  showShortcuts,
  searchOpen,
  openSearch,
  closeSearch,
  clearSelection,
}: CanvasShortcutHandlers) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const tag = target?.tagName
      const modKey = e.metaKey || e.ctrlKey
      const key = e.key.toLowerCase()

      // Cmd+F: open canvas search — intercept even inside inputs
      if (modKey && key === "f" && !e.shiftKey) {
        e.preventDefault()
        openSearch()
        return
      }

      // Don't intercept when typing in an input/textarea or contentEditable
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return
      if (target?.isContentEditable) return

      // Undo: Cmd+Z (without Shift)
      if (modKey && key === "z" && !e.shiftKey) {
        e.preventDefault()
        undo()
        return
      }

      // Redo: Cmd+Shift+Z or Cmd+Y
      if (modKey && ((key === "z" && e.shiftKey) || key === "y")) {
        e.preventDefault()
        redo()
        return
      }

      // Copy: Cmd+C
      if (modKey && key === "c" && !e.shiftKey) {
        e.preventDefault()
        copySelected()
        return
      }

      // Paste: Cmd+V
      if (modKey && key === "v" && !e.shiftKey) {
        e.preventDefault()
        paste()
        return
      }

      // Select all: Cmd+A
      if (modKey && key === "a" && !e.shiftKey) {
        e.preventDefault()
        selectAll()
        return
      }

      // Duplicate: Cmd+D
      if (modKey && key === "d" && !e.shiftKey) {
        e.preventDefault()
        duplicateSelected()
        return
      }

      // Show shortcuts dialog: ?
      if (e.key === "?" && !modKey) {
        e.preventDefault()
        showShortcuts()
        return
      }

      // Escape: close search first, then deselect all nodes and close config panel
      if (e.key === "Escape") {
        e.preventDefault()
        if (searchOpen) {
          closeSearch()
          return
        }
        clearSelection()
        return
      }

      // Delete selected nodes/edges: Backspace or Delete
      if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault()
        deleteSelected()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [
    undo,
    redo,
    deleteSelected,
    copySelected,
    paste,
    selectAll,
    duplicateSelected,
    showShortcuts,
    searchOpen,
    openSearch,
    closeSearch,
    clearSelection,
  ])
}

import { useState, useCallback, useMemo, useRef, useEffect } from "react"
import { useTranslations } from "next-intl"

export interface SlashCommand {
  id: string
  icon: string // lucide icon name or emoji
  hasSubMenu?: boolean
}

export interface SlashSubItem {
  id: string
  label: string
  description?: string
  icon?: string
}

interface UseSlashCommandsOptions {
  query: string
  isComposing: boolean
  agents: { id: string; name: string; icon?: string | null }[]
  mode: "react" | "dag" | "auto"
  isRunning: boolean
  onNewChat: () => void
  onAgentChange: (agentId: string | null) => void
  onModeChange: (mode: "react" | "dag" | "auto") => void
  onQueryChange: (q: string) => void
  onAbort?: () => void
}

type CommandId = "new" | "agent" | "mode"

export function useSlashCommands({
  query,
  isComposing,
  agents,
  isRunning,
  onNewChat,
  onAgentChange,
  onModeChange,
  onQueryChange,
  onAbort,
}: UseSlashCommandsOptions) {
  const t = useTranslations("playground")
  const [selectedIndex, setSelectedIndex] = useState(0)
  const selectedIndexRef = useRef(0)

  // Detect slash at position 0
  const isSlash = !isComposing && query.startsWith("/")
  const searchTerm = isSlash ? query.slice(1) : ""

  // Check if we're in a sub-menu: "/agent " or "/mode "
  const subMenuCommand = useMemo<CommandId | null>(() => {
    if (!isSlash) return null
    if (searchTerm.startsWith("agent ")) return "agent"
    if (searchTerm.startsWith("mode ")) return "mode"
    return null
  }, [isSlash, searchTerm])

  const subMenuSearch = useMemo(() => {
    if (!subMenuCommand) return ""
    return searchTerm.slice(subMenuCommand.length + 1).toLowerCase()
  }, [subMenuCommand, searchTerm])

  // Build available commands (filter out /agent if no agents)
  const availableCommands = useMemo<CommandId[]>(() => {
    const cmds: CommandId[] = ["new"]
    if (agents.length > 0) cmds.push("agent")
    cmds.push("mode")
    return cmds
  }, [agents.length])

  // Filter commands by search term
  const filteredCommands = useMemo(() => {
    if (subMenuCommand) return [] // in sub-menu mode, don't show top-level
    if (!isSlash) return []
    const term = searchTerm.toLowerCase()
    return availableCommands.filter((id) => id.startsWith(term))
  }, [isSlash, searchTerm, availableCommands, subMenuCommand])

  // Sub-menu items
  const subMenuItems = useMemo<SlashSubItem[]>(() => {
    if (!subMenuCommand) return []
    if (subMenuCommand === "agent") {
      const items: SlashSubItem[] = [
        { id: "__none__", label: t("autoAgent") },
        ...agents.map((a) => ({
          id: a.id,
          label: a.name,
          icon: a.icon ?? undefined,
        })),
      ]
      if (subMenuSearch) {
        return items.filter((i) =>
          i.label.toLowerCase().includes(subMenuSearch),
        )
      }
      return items
    }
    if (subMenuCommand === "mode") {
      const items: SlashSubItem[] = [
        { id: "auto", label: `${t("modeAuto")} (Auto)` },
        { id: "react", label: `${t("modeStandard")} (ReAct)` },
        { id: "dag", label: `${t("modePlanner")} (DAG)` },
      ]
      if (subMenuSearch) {
        return items.filter(
          (i) =>
            i.id.includes(subMenuSearch) ||
            i.label.toLowerCase().includes(subMenuSearch),
        )
      }
      return items
    }
    return []
  }, [subMenuCommand, subMenuSearch, agents, t])

  // The items currently visible in the menu
  const visibleItems = subMenuCommand ? subMenuItems : filteredCommands
  const isOpen = isSlash && visibleItems.length > 0

  // Keep selectedIndex in bounds
  useEffect(() => {
    if (selectedIndex >= visibleItems.length) {
      const newIdx = Math.max(0, visibleItems.length - 1)
      setSelectedIndex(newIdx)
      selectedIndexRef.current = newIdx
    }
  }, [visibleItems.length, selectedIndex])

  // Reset index when menu opens/closes or search changes
  useEffect(() => {
    setSelectedIndex(0)
    selectedIndexRef.current = 0
  }, [searchTerm, subMenuCommand])

  const executeCommand = useCallback(
    (commandId: string, subValue?: string) => {
      switch (commandId) {
        case "new":
          if (isRunning && onAbort) onAbort()
          onNewChat()
          break
        case "agent":
          if (subValue === "__none__") {
            onAgentChange(null)
          } else if (subValue) {
            onAgentChange(subValue)
          }
          break
        case "mode":
          if (subValue === "react" || subValue === "dag" || subValue === "auto") {
            onModeChange(subValue)
          }
          break
      }
      onQueryChange("")
    },
    [isRunning, onAbort, onNewChat, onAgentChange, onModeChange, onQueryChange],
  )

  // Keyboard handler — returns true if the event was consumed
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>): boolean => {
      if (!isOpen) return false

      if (e.key === "Escape") {
        e.preventDefault()
        // In sub-menu → go back to top-level; at top-level → close menu
        onQueryChange(subMenuCommand ? "/" : "")
        return true
      }

      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedIndex((prev) => {
          const next = prev <= 0 ? visibleItems.length - 1 : prev - 1
          selectedIndexRef.current = next
          return next
        })
        return true
      }

      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedIndex((prev) => {
          const next = prev >= visibleItems.length - 1 ? 0 : prev + 1
          selectedIndexRef.current = next
          return next
        })
        return true
      }

      if (e.key === "Tab" || e.key === "Enter") {
        e.preventDefault()
        const idx = selectedIndexRef.current
        if (subMenuCommand) {
          // In sub-menu — execute with the selected sub-item
          const item = subMenuItems[idx]
          if (item) {
            executeCommand(subMenuCommand, item.id)
          }
        } else {
          // Top-level command
          const cmdId = filteredCommands[idx]
          if (cmdId) {
            // Commands with sub-menu: enter sub-menu instead of executing
            if (cmdId === "agent" || cmdId === "mode") {
              onQueryChange(`/${cmdId} `)
            } else {
              executeCommand(cmdId)
            }
          }
        }
        return true
      }

      return false
    },
    [isOpen, visibleItems.length, subMenuCommand, subMenuItems, filteredCommands, executeCommand, onQueryChange],
  )

  return {
    isOpen,
    searchTerm,
    filteredCommands,
    subMenuCommand,
    subMenuItems,
    selectedIndex,
    executeCommand,
    handleKeyDown,
    close: useCallback(() => onQueryChange(""), [onQueryChange]),
  }
}

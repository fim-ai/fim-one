import { useTranslations } from "next-intl"
import { Plus, Bot, GitBranch } from "lucide-react"
import { cn } from "@/lib/utils"

interface SlashSubItem {
  id: string
  label: string
  description?: string
  icon?: string
}

interface SlashCommandMenuProps {
  isOpen: boolean
  filteredCommands: string[]
  subMenuCommand: string | null
  subMenuItems: SlashSubItem[]
  selectedIndex: number
  onSelect: (commandId: string, subValue?: string) => void
  onQueryChange: (q: string) => void
}

const COMMAND_ICONS: Record<string, React.ReactNode> = {
  new: <Plus className="h-4 w-4" />,
  agent: <Bot className="h-4 w-4" />,
  mode: <GitBranch className="h-4 w-4" />,
}

export function SlashCommandMenu({
  isOpen,
  filteredCommands,
  subMenuCommand,
  subMenuItems,
  selectedIndex,
  onSelect,
  onQueryChange,
}: SlashCommandMenuProps) {
  const t = useTranslations("playground")

  if (!isOpen) return null

  // Sub-menu rendering (agents or modes)
  if (subMenuCommand) {
    return (
      <div
        className="absolute bottom-full left-0 z-50 mb-2 w-full max-h-[240px] overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
        role="listbox"
      >
        <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
          /{subMenuCommand}
        </div>
        {subMenuItems.length === 0 ? (
          <div className="px-2 py-3 text-center text-sm text-muted-foreground">
            {t("slash.noMatch")}
          </div>
        ) : (
          subMenuItems.map((item, i) => (
            <div
              key={item.id}
              role="option"
              aria-selected={i === selectedIndex}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors",
                i === selectedIndex
                  ? "bg-accent text-accent-foreground"
                  : "hover:bg-accent/50"
              )}
              onMouseDown={(e) => {
                e.preventDefault() // keep textarea focus
                onSelect(subMenuCommand, item.id)
              }}
              onMouseEnter={() => {
                // visual hover handled by CSS, selection by keyboard
              }}
            >
              {item.icon ? (
                <span className="text-sm leading-none shrink-0">{item.icon}</span>
              ) : subMenuCommand === "agent" ? (
                <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
              ) : (
                <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate">{item.label}</span>
            </div>
          ))
        )}
      </div>
    )
  }

  // Top-level command menu
  return (
    <div
      className="absolute bottom-full left-0 z-50 mb-2 w-full max-h-[240px] overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
      role="listbox"
    >
      {filteredCommands.map((id, i) => (
        <div
          key={id}
          role="option"
          aria-selected={i === selectedIndex}
          className={cn(
            "flex cursor-pointer items-center gap-3 rounded-sm px-2 py-2 text-sm transition-colors",
            i === selectedIndex
              ? "bg-accent text-accent-foreground"
              : "hover:bg-accent/50"
          )}
          onMouseDown={(e) => {
            e.preventDefault()
            if (id === "agent" || id === "mode") {
              onQueryChange(`/${id} `)
            } else {
              onSelect(id)
            }
          }}
        >
          <span className="shrink-0 text-muted-foreground">
            {COMMAND_ICONS[id]}
          </span>
          <div className="flex flex-col min-w-0">
            <span className="font-medium">/{id}</span>
            <span className="text-xs text-muted-foreground truncate">
              {t(`slash.${id}Desc` as Parameters<typeof t>[0])}
            </span>
          </div>
          {(id === "agent" || id === "mode") && (
            <span className="ml-auto text-xs text-muted-foreground">▸</span>
          )}
        </div>
      ))}
    </div>
  )
}

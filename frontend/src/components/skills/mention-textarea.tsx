"use client"

import {
  useState,
  useRef,
  useCallback,
  useEffect,
  type KeyboardEvent,
  type ChangeEvent,
} from "react"
import { createPortal } from "react-dom"
import { useTranslations } from "next-intl"
import { Cable, Server, BookOpen, Bot } from "lucide-react"
import { Textarea } from "@/components/ui/textarea"
import type { ResourceRef, ResourceRefType } from "@/types/skill"

interface MentionTextareaProps {
  id?: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  className?: string
  resourceRefs: ResourceRef[]
}

const TYPE_ICONS: Record<ResourceRefType, React.ReactNode> = {
  connector: <Cable className="h-3.5 w-3.5" />,
  mcp_server: <Server className="h-3.5 w-3.5" />,
  knowledge_base: <BookOpen className="h-3.5 w-3.5" />,
  agent: <Bot className="h-3.5 w-3.5" />,
}

/**
 * Textarea with keyboard-driven @mention autocomplete.
 * Dropdown is Portal'd to body for correct positioning.
 * Use arrow keys + Enter/Tab to select, Escape to dismiss.
 */
export function MentionTextarea({
  id,
  value,
  onChange,
  placeholder,
  className,
  resourceRefs,
}: MentionTextareaProps) {
  const t = useTranslations("skills")
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const [showDropdown, setShowDropdown] = useState(false)
  const [mentionQuery, setMentionQuery] = useState("")
  const [mentionStart, setMentionStart] = useState(-1)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [dropdownPosition, setDropdownPosition] = useState({ top: 0, left: 0 })

  const suggestions = resourceRefs.filter((ref) => {
    if (!mentionQuery) return true
    const q = mentionQuery.toLowerCase()
    return ref.alias.toLowerCase().includes(q) || ref.name.toLowerCase().includes(q)
  })

  // Track caret position via mirror element → viewport coords
  const updateDropdownPosition = useCallback(() => {
    const textarea = textareaRef.current
    if (!textarea) return

    const rect = textarea.getBoundingClientRect()
    const style = window.getComputedStyle(textarea)
    const lineHeight = parseInt(style.lineHeight || "20")

    // Mirror the textarea to find where the caret is
    const mirror = document.createElement("div")
    const cssProps = [
      "fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing",
      "wordSpacing", "textIndent", "whiteSpace", "wordWrap", "overflowWrap",
      "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
      "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
    ] as const
    mirror.style.position = "absolute"
    mirror.style.visibility = "hidden"
    mirror.style.width = `${textarea.clientWidth}px`
    mirror.style.height = "auto"
    for (const p of cssProps) mirror.style[p] = style[p]

    mirror.textContent = value.slice(0, textarea.selectionStart)
    const marker = document.createElement("span")
    marker.textContent = "|"
    mirror.appendChild(marker)
    document.body.appendChild(mirror)

    const mirrorRect = mirror.getBoundingClientRect()
    const markerRect = marker.getBoundingClientRect()
    const caretY = markerRect.top - mirrorRect.top
    const caretX = markerRect.left - mirrorRect.left
    document.body.removeChild(mirror)

    // Convert to viewport-fixed coords
    let top = rect.top + caretY + lineHeight + 4 - textarea.scrollTop
    let left = rect.left + caretX

    // Clamp to viewport
    if (top + 200 > window.innerHeight) top = rect.top + caretY - 200 - textarea.scrollTop
    left = Math.max(8, Math.min(left, window.innerWidth - 250))
    top = Math.max(8, top)

    setDropdownPosition({ top, left })
  }, [value])

  const handleChange = useCallback(
    (e: ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value
      onChange(newValue)

      const cursorPos = e.target.selectionStart
      const textBefore = newValue.slice(0, cursorPos)
      const lastAt = textBefore.lastIndexOf("@")

      if (lastAt >= 0) {
        const charBefore = lastAt > 0 ? textBefore[lastAt - 1] : "\n"
        if (!/[a-zA-Z0-9]/.test(charBefore)) {
          const query = textBefore.slice(lastAt + 1)
          if (!/\s/.test(query) && query.length <= 30) {
            setMentionStart(lastAt)
            setMentionQuery(query)
            setShowDropdown(true)
            setSelectedIndex(0)
            return
          }
        }
      }
      setShowDropdown(false)
    },
    [onChange],
  )

  useEffect(() => {
    if (showDropdown) updateDropdownPosition()
  }, [showDropdown, updateDropdownPosition])

  const insertMention = useCallback(
    (ref: ResourceRef) => {
      const textarea = textareaRef.current
      if (!textarea || mentionStart < 0) return

      const before = value.slice(0, mentionStart)
      const cursorPos = textarea.selectionStart
      const after = value.slice(cursorPos)
      const aliasText = ref.alias + " "
      onChange(before + aliasText + after)
      setShowDropdown(false)

      requestAnimationFrame(() => {
        textarea.focus()
        textarea.setSelectionRange(mentionStart + aliasText.length, mentionStart + aliasText.length)
      })
    },
    [value, mentionStart, onChange],
  )

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!showDropdown || suggestions.length === 0) return
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSelectedIndex((prev) => (prev + 1) % suggestions.length)
      } else if (e.key === "ArrowUp") {
        e.preventDefault()
        setSelectedIndex((prev) => (prev - 1 + suggestions.length) % suggestions.length)
      } else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        insertMention(suggestions[selectedIndex])
      } else if (e.key === "Escape") {
        e.preventDefault()
        setShowDropdown(false)
      }
    },
    [showDropdown, suggestions, selectedIndex, insertMention],
  )

  return (
    <div>
      <Textarea
        ref={textareaRef}
        id={id}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={className}
      />

      {resourceRefs.length > 0 && (
        <p className="text-[11px] text-muted-foreground mt-1">
          {t("mentionHint")}
        </p>
      )}

      {showDropdown && suggestions.length > 0 && createPortal(
        <div
          className="fixed z-[100] w-60 rounded-md border border-border/60 bg-popover/95 backdrop-blur-md shadow-lg overflow-hidden pointer-events-none"
          style={{ top: dropdownPosition.top, left: dropdownPosition.left }}
        >
          <div className="max-h-[180px] overflow-y-auto py-1">
            {suggestions.map((ref, index) => (
              <div
                key={`${ref.type}:${ref.id}`}
                className={`flex items-center gap-2 w-full px-2.5 py-1.5 text-sm transition-colors ${
                  index === selectedIndex
                    ? "bg-accent text-accent-foreground"
                    : "text-popover-foreground"
                }`}
              >
                <span className="text-muted-foreground shrink-0">
                  {TYPE_ICONS[ref.type]}
                </span>
                <span className="font-mono text-primary font-medium shrink-0 text-xs">
                  {ref.alias}
                </span>
                <span className="text-muted-foreground truncate text-xs">
                  {ref.name}
                </span>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}

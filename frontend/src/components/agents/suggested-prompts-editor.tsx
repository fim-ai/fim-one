"use client"

import { ArrowUp, ArrowDown, X, Plus } from "lucide-react"

interface SuggestedPromptsEditorProps {
  value: string[]
  onChange: (value: string[]) => void
}

const inputClass =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"

export function SuggestedPromptsEditor({
  value,
  onChange,
}: SuggestedPromptsEditorProps) {
  const updateItem = (index: number, text: string) => {
    const next = [...value]
    next[index] = text
    onChange(next)
  }

  const removeItem = (index: number) => {
    onChange(value.filter((_, i) => i !== index))
  }

  const moveItem = (index: number, direction: -1 | 1) => {
    const target = index + direction
    if (target < 0 || target >= value.length) return
    const next = [...value]
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }

  const addItem = () => {
    onChange([...value, ""])
  }

  return (
    <div className="space-y-2">
      {value.map((prompt, index) => (
        <div key={index} className="flex items-center gap-1">
          {/* Reorder buttons */}
          <div className="flex flex-col">
            <button
              type="button"
              onClick={() => moveItem(index, -1)}
              disabled={index === 0}
              className="h-4 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-0 disabled:pointer-events-none transition-colors"
              aria-label="Move up"
            >
              <ArrowUp className="h-3 w-3" />
            </button>
            <button
              type="button"
              onClick={() => moveItem(index, 1)}
              disabled={index === value.length - 1}
              className="h-4 w-7 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent disabled:opacity-0 disabled:pointer-events-none transition-colors"
              aria-label="Move down"
            >
              <ArrowDown className="h-3 w-3" />
            </button>
          </div>

          {/* Text input */}
          <input
            type="text"
            value={prompt}
            onChange={(e) => updateItem(index, e.target.value)}
            placeholder="Enter a suggested prompt..."
            className={inputClass}
          />

          {/* Delete button */}
          <button
            type="button"
            onClick={() => removeItem(index)}
            className="h-7 w-7 flex-shrink-0 flex items-center justify-center rounded-sm text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
            aria-label="Remove prompt"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}

      <button
        type="button"
        onClick={addItem}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors py-1"
      >
        <Plus className="h-3.5 w-3.5" />
        Add prompt
      </button>
    </div>
  )
}

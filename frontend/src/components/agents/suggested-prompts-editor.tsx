"use client"

import { useRef, useState, useEffect } from "react"
import { useTranslations } from "next-intl"
import { X, Plus, GripVertical } from "lucide-react"
import { Input } from "@/components/ui/input"
import {
  DndContext,
  closestCenter,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"

interface SuggestedPromptsEditorProps {
  value: string[]
  onChange: (value: string[]) => void
}

interface SortableItemProps {
  id: string
  prompt: string
  onUpdate: (text: string) => void
  onRemove: () => void
}

function SortableItem({ id, prompt, onUpdate, onRemove }: SortableItemProps) {
  const t = useTranslations("agents")
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  return (
    <div ref={setNodeRef} style={style} className="flex items-center gap-1">
      <button
        type="button"
        className="flex-shrink-0 h-7 w-6 flex items-center justify-center rounded-sm text-muted-foreground hover:text-foreground hover:bg-accent transition-colors cursor-grab active:cursor-grabbing"
        aria-label={t("dragToReorder")}
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </button>

      <Input
        type="text"
        value={prompt}
        onChange={(e) => onUpdate(e.target.value)}
        placeholder={t("promptPlaceholder")}
      />

      <button
        type="button"
        onClick={onRemove}
        className="h-7 w-7 flex-shrink-0 flex items-center justify-center rounded-sm text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
        aria-label={t("removePrompt")}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

export function SuggestedPromptsEditor({
  value,
  onChange,
}: SuggestedPromptsEditorProps) {
  const t = useTranslations("agents")
  const sensors = useSensors(useSensor(PointerSensor))

  // Stable IDs: each item gets a unique ID on creation, independent of its index
  const counterRef = useRef(0)
  const genId = () => `p-${++counterRef.current}`
  const [ids, setIds] = useState<string[]>(() => value.map(() => genId()))

  // Sync ids when value is loaded externally (e.g. async API response)
  useEffect(() => {
    setIds((prev) => {
      if (prev.length === value.length) return prev
      if (value.length > prev.length) {
        return [...prev, ...Array.from({ length: value.length - prev.length }, () => genId())]
      }
      return prev.slice(0, value.length)
    })
  }, [value.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (over && active.id !== over.id) {
      const oldIndex = ids.indexOf(active.id as string)
      const newIndex = ids.indexOf(over.id as string)
      setIds(arrayMove(ids, oldIndex, newIndex))
      onChange(arrayMove(value, oldIndex, newIndex))
    }
  }

  const updateItem = (index: number, text: string) => {
    const next = [...value]
    next[index] = text
    onChange(next)
  }

  const removeItem = (index: number) => {
    setIds(ids.filter((_, i) => i !== index))
    onChange(value.filter((_, i) => i !== index))
  }

  const addItem = () => {
    setIds([...ids, genId()])
    onChange([...value, ""])
  }

  return (
    <div className="space-y-2">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {ids.map((id, index) => (
            <SortableItem
              key={id}
              id={id}
              prompt={value[index] ?? ""}
              onUpdate={(text) => updateItem(index, text)}
              onRemove={() => removeItem(index)}
            />
          ))}
        </SortableContext>
      </DndContext>

      <button
        type="button"
        onClick={addItem}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors py-1"
      >
        <Plus className="h-3.5 w-3.5" />
        {t("addPrompt")}
      </button>
    </div>
  )
}

"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  BookOpen,
  FileCode2,
  Globe,
  GitBranch,
  Loader2,
  LayoutTemplate,
  Boxes,
  MessageSquare,
  Check,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { workflowApi } from "@/lib/api"
import type { WorkflowTemplate } from "@/types/workflow"

const categoryIcons: Record<string, React.ReactNode> = {
  basic: <MessageSquare className="h-5 w-5" />,
  intermediate: <Boxes className="h-5 w-5" />,
  advanced: <LayoutTemplate className="h-5 w-5" />,
  ai: <BookOpen className="h-5 w-5" />,
  integration: <Globe className="h-5 w-5" />,
  data: <FileCode2 className="h-5 w-5" />,
}

interface TemplateGalleryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function TemplateGalleryDialog({
  open,
  onOpenChange,
}: TemplateGalleryDialogProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const router = useRouter()

  const [templates, setTemplates] = useState<WorkflowTemplate[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [activeCategory, setActiveCategory] = useState("all")

  const loadTemplates = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await workflowApi.getTemplates()
      setTemplates(data)
    } catch {
      setTemplates([])
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      loadTemplates()
      setSelectedId(null)
      setActiveCategory("all")
    }
  }, [open, loadTemplates])

  // Extract unique categories from templates
  const categories = useMemo(() => {
    const cats = new Set(templates.map((tmpl) => tmpl.category))
    return Array.from(cats).sort()
  }, [templates])

  // Filter templates by active category
  const filteredTemplates = useMemo(() => {
    if (activeCategory === "all") return templates
    return templates.filter((tmpl) => tmpl.category === activeCategory)
  }, [templates, activeCategory])

  const selectedTemplate = useMemo(
    () => templates.find((tmpl) => tmpl.id === selectedId) ?? null,
    [templates, selectedId],
  )

  const handleCreate = async () => {
    if (!selectedId) return
    setIsCreating(true)
    try {
      const workflow = await workflowApi.createFromTemplate(selectedId)
      onOpenChange(false)
      toast.success(t("templateCreateSuccess"))
      router.push(`/workflows/${workflow.id}`)
    } catch {
      toast.error(t("templateCreateFailed"))
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <LayoutTemplate className="h-4 w-4" />
            {t("galleryTitle")}
          </DialogTitle>
          <DialogDescription>
            {t("galleryDescription")}
          </DialogDescription>
        </DialogHeader>

        {/* Category filter tabs */}
        {!isLoading && categories.length > 0 && (
          <Tabs
            value={activeCategory}
            onValueChange={setActiveCategory}
            className="w-full"
          >
            <TabsList className="w-full justify-start flex-wrap h-auto gap-1">
              <TabsTrigger value="all" className="text-xs">
                {tc("all")}
              </TabsTrigger>
              {categories.map((cat) => (
                <TabsTrigger key={cat} value={cat} className="text-xs gap-1">
                  {categoryIcons[cat] ? (
                    <span className="[&_svg]:h-3.5 [&_svg]:w-3.5">
                      {categoryIcons[cat]}
                    </span>
                  ) : null}
                  {t(`templateCategory_${cat}` as Parameters<typeof t>[0])}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        )}

        {/* Template grid */}
        {isLoading ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="rounded-lg border p-4 space-y-3">
                <Skeleton className="h-10 w-10 rounded-md" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            ))}
          </div>
        ) : filteredTemplates.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <LayoutTemplate className="h-10 w-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">
              {t("galleryEmpty")}
            </p>
          </div>
        ) : (
          <ScrollArea className="max-h-[400px]">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 pr-3">
              {filteredTemplates.map((tmpl) => {
                const isSelected = selectedId === tmpl.id
                const nodeCount = tmpl.blueprint?.nodes?.length ?? 0
                return (
                  <button
                    key={tmpl.id}
                    type="button"
                    className={cn(
                      "relative text-left rounded-lg border p-4 transition-all",
                      isSelected
                        ? "border-ring bg-accent/20 ring-1 ring-ring"
                        : "border-border hover:border-ring/40 hover:bg-accent/10",
                    )}
                    onClick={() => setSelectedId(isSelected ? null : tmpl.id)}
                  >
                    {/* Selected indicator */}
                    {isSelected && (
                      <div className="absolute top-2 right-2 flex h-5 w-5 items-center justify-center rounded-full bg-primary">
                        <Check className="h-3 w-3 text-primary-foreground" />
                      </div>
                    )}

                    {/* Icon */}
                    <div className="flex h-10 w-10 items-center justify-center rounded-md bg-muted mb-3">
                      {tmpl.icon ? (
                        <span className="text-lg">{tmpl.icon}</span>
                      ) : (
                        <span className="text-muted-foreground">
                          {categoryIcons[tmpl.category] ?? (
                            <GitBranch className="h-5 w-5" />
                          )}
                        </span>
                      )}
                    </div>

                    {/* Name */}
                    <p className="text-sm font-medium truncate pr-5">
                      {tmpl.name}
                    </p>

                    {/* Description */}
                    <p className="text-xs text-muted-foreground line-clamp-2 mt-1 min-h-[2rem]">
                      {tmpl.description}
                    </p>

                    {/* Footer: category badge + node count */}
                    <div className="flex items-center gap-2 mt-3">
                      <Badge variant="secondary" className="text-[10px]">
                        {t(
                          `templateCategory_${tmpl.category}` as Parameters<
                            typeof t
                          >[0],
                        )}
                      </Badge>
                      {nodeCount > 0 && (
                        <span className="text-[10px] text-muted-foreground">
                          {t("nodeCount", { count: nodeCount })}
                        </span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          </ScrollArea>
        )}

        {/* Footer actions */}
        <div className="flex items-center justify-between pt-2">
          <div className="text-xs text-muted-foreground">
            {selectedTemplate ? selectedTemplate.name : t("gallerySelectHint")}
          </div>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={isCreating}
            >
              {tc("cancel")}
            </Button>
            <Button
              onClick={handleCreate}
              disabled={!selectedId || isCreating}
              className="gap-1.5"
            >
              {isCreating && (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              )}
              {tc("create")}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

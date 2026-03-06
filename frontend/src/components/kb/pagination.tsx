"use client"

import { useTranslations } from "next-intl"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface PaginationProps {
  page: number
  totalPages: number
  onPageChange: (page: number) => void
  className?: string
}

/**
 * Build the list of page numbers (and ellipsis markers) to display.
 *
 * Rules:
 * - Always show first and last page
 * - Show up to 2 siblings around the current page
 * - Insert "..." where pages are skipped
 * - When totalPages <= 7, show all pages (no ellipsis needed)
 */
function getPageNumbers(
  current: number,
  total: number,
): (number | "ellipsis")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }

  const pages: (number | "ellipsis")[] = []

  // Always include page 1
  pages.push(1)

  // Left ellipsis
  if (current > 4) {
    pages.push("ellipsis")
  }

  // Sibling range around current
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)

  for (let i = start; i <= end; i++) {
    pages.push(i)
  }

  // Right ellipsis
  if (current < total - 3) {
    pages.push("ellipsis")
  }

  // Always include last page
  if (total > 1) {
    pages.push(total)
  }

  return pages
}

export function Pagination({
  page,
  totalPages,
  onPageChange,
  className,
}: PaginationProps) {
  const t = useTranslations("kb")

  if (totalPages <= 1) return null

  const items = getPageNumbers(page, totalPages)

  return (
    <div className={cn("flex items-center justify-center gap-1", className)}>
      {/* Previous */}
      <Button
        variant="outline"
        size="icon-xs"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        aria-label={t("previousPage")}
      >
        <ChevronLeft className="h-3.5 w-3.5" />
      </Button>

      {/* Page numbers */}
      {items.map((item, idx) =>
        item === "ellipsis" ? (
          <span
            key={`ellipsis-${idx}`}
            className="flex items-center justify-center w-6 h-6 text-xs text-muted-foreground select-none"
          >
            ...
          </span>
        ) : (
          <Button
            key={item}
            variant={item === page ? "default" : "outline"}
            size="icon-xs"
            onClick={() => onPageChange(item)}
            aria-label={t("page", { page: item })}
            aria-current={item === page ? "page" : undefined}
          >
            <span className="text-[11px] tabular-nums">{item}</span>
          </Button>
        ),
      )}

      {/* Next */}
      <Button
        variant="outline"
        size="icon-xs"
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        aria-label={t("nextPage")}
      >
        <ChevronRight className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}

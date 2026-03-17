"use client"

import { useTranslations } from "next-intl"
import { ChevronLeft, ChevronRight } from "lucide-react"

interface ListPaginationProps {
  currentPage: number
  totalPages: number
  onPageChange: (page: number) => void
}

export function ListPagination({
  currentPage,
  totalPages,
  onPageChange,
}: ListPaginationProps) {
  const tc = useTranslations("common")

  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-center gap-3 pt-6">
      <button
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage <= 1}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>
      <span className="text-sm text-muted-foreground tabular-nums">
        {tc("pageInfo", { current: currentPage, total: totalPages })}
      </span>
      <button
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage >= totalPages}
        className="flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors disabled:opacity-30 disabled:pointer-events-none"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  )
}

export const PAGE_SIZE = 12

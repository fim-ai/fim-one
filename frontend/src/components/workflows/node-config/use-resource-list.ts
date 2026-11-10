"use client"

import { useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error-utils"
import { listAllPages, type Page } from "./resource-options"

/**
 * Everything a listing has, loaded once when the form opens. `list` must be
 * a stable function (an API method, not a closure made during render).
 * A failure is reported; the form then shows only what the step holds.
 */
export function useResourceList<T>(
  list: (page: number, size: number) => Promise<Page<T>>,
): T[] {
  const tError = useTranslations("errors")
  const [items, setItems] = useState<T[]>([])
  useEffect(() => {
    let cancelled = false
    listAllPages(list)
      .then((all) => {
        if (!cancelled) setItems(all)
      })
      .catch((err) => {
        if (!cancelled) toast.error(getErrorMessage(err, tError))
      })
    return () => {
      cancelled = true
    }
  }, [list, tError])
  return items
}

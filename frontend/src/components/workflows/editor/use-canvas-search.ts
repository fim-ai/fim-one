import { useCallback, useEffect, useMemo, useState } from "react"
import type { RefObject } from "react"
import type { Node, ReactFlowInstance } from "@xyflow/react"

import { findSearchMatches } from "./canvas-model"

/** Find-on-canvas: the query, the nodes it matches and which one is current. */
export function useCanvasSearch(nodes: Node[], rfInstanceRef: RefObject<ReactFlowInstance | null>) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [index, setIndex] = useState(0)

  const matches = useMemo(() => findSearchMatches(nodes, query), [nodes, query])

  // Reset search index when matches change
  useEffect(() => {
    if (index >= matches.length) {
      setIndex(0)
    }
  }, [matches.length, index])

  const openSearch = useCallback(() => {
    setOpen(true)
  }, [])

  const closeSearch = useCallback(() => {
    setOpen(false)
    setQuery("")
    setIndex(0)
  }, [])

  const changeQuery = useCallback((next: string) => {
    setQuery(next)
    setIndex(0)
  }, [])

  const focusMatch = useCallback(
    (target: Node | undefined) => {
      if (target && rfInstanceRef.current) {
        rfInstanceRef.current.fitView({
          nodes: [{ id: target.id }],
          duration: 300,
          padding: 1.5,
          maxZoom: 1.2,
        })
      }
    },
    [rfInstanceRef],
  )

  const next = useCallback(() => {
    if (matches.length === 0) return
    const nextIndex = (index + 1) % matches.length
    setIndex(nextIndex)
    focusMatch(matches[nextIndex])
  }, [matches, index, focusMatch])

  const prev = useCallback(() => {
    if (matches.length === 0) return
    const prevIndex = (index - 1 + matches.length) % matches.length
    setIndex(prevIndex)
    focusMatch(matches[prevIndex])
  }, [matches, index, focusMatch])

  // Build search match ID set + current match ID for styling
  const matchIds = useMemo(() => new Set(matches.map((n) => n.id)), [matches])
  const currentId = matches.length > 0 ? matches[index]?.id ?? null : null
  const active = open && query.trim().length > 0

  return {
    open,
    query,
    index,
    matchCount: matches.length,
    matchIds,
    currentId,
    active,
    openSearch,
    closeSearch,
    changeQuery,
    next,
    prev,
  }
}

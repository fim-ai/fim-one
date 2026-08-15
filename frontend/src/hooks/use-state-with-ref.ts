"use client"

import { useCallback, useRef, useState } from "react"

/**
 * `useState` with a ref mirror that is written synchronously by the setter.
 *
 * The usual `useEffect(() => { ref.current = value }, [value])` mirror only
 * catches up after React commits. Async work that resumes in the same
 * microtask as its own state update — a send waiting on an in-flight upload,
 * for instance — reads the pre-update snapshot from such a ref and acts on
 * stale data. Reading `ref.current` from this hook always sees the latest
 * value; render still uses the state value.
 */
export function useStateWithRef<T>(
  initialValue: T,
): [T, (value: T | ((prev: T) => T)) => void, { current: T }] {
  const [value, setValue] = useState<T>(initialValue)
  const ref = useRef<T>(initialValue)

  const set = useCallback((next: T | ((prev: T) => T)) => {
    const resolved = typeof next === "function" ? (next as (prev: T) => T)(ref.current) : next
    ref.current = resolved
    setValue(resolved)
  }, [])

  return [value, set, ref]
}

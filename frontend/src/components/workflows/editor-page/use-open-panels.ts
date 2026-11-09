import { useCallback, useRef, useState } from "react"

/**
 * Which of a fixed set of dialogs / sheets are open.
 *
 * `opener(name)` and `setter(name)` return the same function for a given
 * name on every render, so they can be passed as props without
 * re-triggering effects that depend on them.
 */
export function useOpenPanels<Name extends string>() {
  const [open, setOpen] = useState<ReadonlySet<Name>>(() => new Set())

  const set = useCallback((name: Name, isOpen: boolean) => {
    setOpen((prev) => {
      if (prev.has(name) === isOpen) return prev
      const next = new Set(prev)
      if (isOpen) next.add(name)
      else next.delete(name)
      return next
    })
  }, [])

  const openers = useRef(new Map<Name, () => void>())
  const setters = useRef(new Map<Name, (isOpen: boolean) => void>())

  const opener = (name: Name): (() => void) => {
    let fn = openers.current.get(name)
    if (!fn) {
      fn = () => set(name, true)
      openers.current.set(name, fn)
    }
    return fn
  }

  const setter = (name: Name): ((isOpen: boolean) => void) => {
    let fn = setters.current.get(name)
    if (!fn) {
      fn = (isOpen: boolean) => set(name, isOpen)
      setters.current.set(name, fn)
    }
    return fn
  }

  return { isOpen: (name: Name) => open.has(name), set, opener, setter }
}

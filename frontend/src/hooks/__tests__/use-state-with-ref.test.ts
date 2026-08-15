import { describe, it, expect } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useStateWithRef } from "@/hooks/use-state-with-ref"

describe("useStateWithRef", () => {
  it("exposes the initial value on both state and ref", () => {
    const { result } = renderHook(() => useStateWithRef([1, 2]))
    expect(result.current[0]).toEqual([1, 2])
    expect(result.current[2].current).toEqual([1, 2])
  })

  it("updates the ref synchronously, before React re-renders", () => {
    const { result } = renderHook(() => useStateWithRef<string[]>([]))
    const [, set, ref] = result.current
    // No act(): this is the mid-async-callback case the hook exists for.
    set(["a"])
    expect(ref.current).toEqual(["a"])
    // The rendered value has not caught up yet — that is expected.
    expect(result.current[0]).toEqual([])
  })

  it("renders the value once React commits", () => {
    const { result } = renderHook(() => useStateWithRef<string[]>([]))
    act(() => {
      result.current[1](["a"])
    })
    expect(result.current[0]).toEqual(["a"])
    expect(result.current[2].current).toEqual(["a"])
  })

  it("resolves functional updates against the ref, not a render snapshot", () => {
    const { result } = renderHook(() => useStateWithRef<string[]>([]))
    const [, set, ref] = result.current
    // Two updates inside one commit, as parallel uploads would do.
    act(() => {
      set((prev) => [...prev, "first"])
      set((prev) => [...prev, "second"])
    })
    expect(ref.current).toEqual(["first", "second"])
    expect(result.current[0]).toEqual(["first", "second"])
  })

  it("lets an awaited continuation read what a resolved promise just wrote", async () => {
    const { result } = renderHook(() => useStateWithRef<string[]>([]))
    const [, set, ref] = result.current
    // Mirrors the composer: the upload's .then() records the result, and the
    // send resumes in the next microtask expecting to see it.
    const upload = Promise.resolve().then(() => {
      set((prev) => [...prev, "uploaded"])
    })
    await Promise.allSettled([upload])
    expect(ref.current).toEqual(["uploaded"])
    await act(async () => {})
  })
})

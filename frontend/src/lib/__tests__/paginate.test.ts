import { afterEach, describe, expect, it, vi } from "vitest"

import { adminApi } from "@/lib/api"
import { fetchAllPages } from "@/lib/paginate"

// The admin organizations endpoint refuses size > 100 with 422.
const CAP = 100

function fakeOrgServer(count: number) {
  const orgs = Array.from({ length: count }, (_, i) => ({ id: `org-${i}`, name: `Org ${i}` }))
  const requested: URL[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), "http://test.local")
    requested.push(url)
    const page = Number(url.searchParams.get("page") ?? "1")
    const size = Number(url.searchParams.get("size") ?? "50")
    if (size > CAP) {
      return new Response(JSON.stringify({ detail: [{ msg: "size too large" }] }), { status: 422 })
    }
    const items = orgs.slice((page - 1) * size, page * size)
    const body = { items, total: count, page, size, pages: Math.ceil(count / size) }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })
  })
  return { fetchMock, requested }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("adminApi.listAllOrganizations", () => {
  it("returns all organizations beyond the endpoint's page cap without an oversized request", async () => {
    const { fetchMock, requested } = fakeOrgServer(250)
    vi.stubGlobal("fetch", fetchMock)

    const orgs = await adminApi.listAllOrganizations()

    expect(orgs).toHaveLength(250)
    expect(orgs[249]).toMatchObject({ id: "org-249", name: "Org 249" })
    expect(requested.every((u) => Number(u.searchParams.get("size")) <= CAP)).toBe(true)
    expect(requested.every((u) => u.pathname === "/api/admin/organizations")).toBe(true)
    expect(requested).toHaveLength(3)
  })

  it("makes one request when everything fits on the first page", async () => {
    const { fetchMock, requested } = fakeOrgServer(3)
    vi.stubGlobal("fetch", fetchMock)

    expect(await adminApi.listAllOrganizations()).toHaveLength(3)
    expect(requested).toHaveLength(1)
  })
})

describe("fetchAllPages", () => {
  it("clamps the requested size to the cap", async () => {
    const sizes: number[] = []
    await fetchAllPages(
      async (_page, size) => {
        sizes.push(size)
        return { items: [], pages: 0 }
      },
      { size: 500 },
    )
    expect(sizes).toEqual([100])
  })
})

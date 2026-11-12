/**
 * Read every page of a paginated list endpoint.
 *
 * List endpoints cap their page size (most at 100). A caller that needs the
 * whole set, such as a filter dropdown or an id-to-name lookup, asks for pages
 * of at most that cap until the server reports no more, instead of asking for
 * one oversized page that the server refuses with 422.
 */
export interface PageLike<T> {
  items: T[]
  pages?: number
  total?: number
}

export const MAX_PAGE_SIZE = 100

export async function fetchAllPages<T>(
  fetchPage: (page: number, size: number) => Promise<PageLike<T>>,
  { size = MAX_PAGE_SIZE, maxPages = 1000 }: { size?: number; maxPages?: number } = {},
): Promise<T[]> {
  const pageSize = Math.min(size, MAX_PAGE_SIZE)
  const all: T[] = []
  for (let page = 1; page <= maxPages; page++) {
    const res = await fetchPage(page, pageSize)
    all.push(...res.items)
    const lastPage =
      res.items.length < pageSize ||
      (typeof res.pages === "number" && page >= res.pages) ||
      (typeof res.total === "number" && all.length >= res.total)
    if (lastPage) break
  }
  return all
}

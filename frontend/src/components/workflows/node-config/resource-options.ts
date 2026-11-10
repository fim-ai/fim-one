/** One page of a paginated listing. */
export interface Page<T> {
  items: T[]
  pages: number
}

/** The largest page the listings serve. */
const PAGE_SIZE = 100

/**
 * Everything a listing has, page after page. The listings serve at most 100
 * per request; a user with more would otherwise not find the later ones in
 * a dropdown.
 */
export async function listAllPages<T>(
  list: (page: number, size: number) => Promise<Page<T>>,
): Promise<T[]> {
  const first = await list(1, PAGE_SIZE)
  const all = [...first.items]
  for (let page = 2; page <= first.pages; page++) {
    all.push(...(await list(page, PAGE_SIZE)).items)
  }
  return all
}

export interface SelectOption {
  value: string
  label: string
}

/** What a dropdown needs to know about a listed resource. */
export interface ListedResource {
  id: string
  name: string
  is_active: boolean
}

/**
 * The resources a step can pick, as dropdown options.
 *
 * Disabled ones are left out: the run would refuse them. The one the step
 * already points at always stays in the list: by name when it is among
 * `resources`, by its id when it is not (still loading, deleted, or no
 * longer shared with this user), so the field never shows up empty while
 * the step still holds a reference.
 */
export function resourceOptions(resources: ListedResource[], selectedId: string): SelectOption[] {
  const options = resources
    .filter((r) => r.is_active || r.id === selectedId)
    .map((r) => ({ value: r.id, label: r.name }))
  if (selectedId && !options.some((o) => o.value === selectedId)) {
    options.push({ value: selectedId, label: selectedId })
  }
  return options
}

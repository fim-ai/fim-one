import type { ConnectorActionResponse, ConnectorResponse } from "@/types/connector"

/** One page of a paginated listing. */
interface Page<T> {
  items: T[]
  pages: number
}

/** The largest page the connector listing serves. */
const PAGE_SIZE = 100

/**
 * Every connector the listing has, page after page. The listing serves at
 * most 100 per request; a user with more would otherwise not find the later
 * ones in the dropdown.
 */
export async function listAllConnectors(
  list: (page: number, size: number) => Promise<Page<ConnectorResponse>>,
): Promise<ConnectorResponse[]> {
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

/**
 * Connectors a Connector step can pick.
 *
 * The step calls HTTP actions only, so database connectors are left out, and
 * so are disabled connectors (the run would refuse them). The connector the
 * step already points at always stays in the list: by name when it is among
 * `connectors`, by its id when it is not (still loading, deleted, or no
 * longer shared with this user), so the field never shows up empty while the
 * step still holds a reference.
 */
export function connectorOptions(
  connectors: ConnectorResponse[],
  selectedId: string,
): SelectOption[] {
  const options = connectors
    .filter((c) => c.type !== "database" && (c.is_active || c.id === selectedId))
    .map((c) => ({ value: c.id, label: c.name }))
  if (selectedId && !options.some((o) => o.value === selectedId)) {
    options.push({ value: selectedId, label: selectedId })
  }
  return options
}

/** Actions of the chosen connector; the stored action stays listed like above. */
export function actionOptions(
  connectors: ConnectorResponse[],
  connectorId: string,
  selectedActionId: string,
): SelectOption[] {
  const actions: ConnectorActionResponse[] =
    connectors.find((c) => c.id === connectorId)?.actions ?? []
  const options = actions.map((a) => ({ value: a.id, label: a.name }))
  if (selectedActionId && !options.some((o) => o.value === selectedActionId)) {
    options.push({ value: selectedActionId, label: selectedActionId })
  }
  return options
}

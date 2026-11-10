import type { ConnectorActionResponse, ConnectorResponse } from "@/types/connector"

import { listAllPages, type Page, type SelectOption } from "./resource-options"

export type { SelectOption }

/** Every connector the listing has, page after page. */
export function listAllConnectors(
  list: (page: number, size: number) => Promise<Page<ConnectorResponse>>,
): Promise<ConnectorResponse[]> {
  return listAllPages(list)
}

/**
 * What a Connector step can do with a database connector, in place of an
 * action: the step stores one of these as its `action_id`. The same three
 * operations a chat with the connector gets.
 */
export const DATABASE_OPERATIONS = ["list_tables", "describe_table", "query"] as const
export type DatabaseOperation = (typeof DATABASE_OPERATIONS)[number]

export function isDatabaseOperation(value: string): value is DatabaseOperation {
  return (DATABASE_OPERATIONS as readonly string[]).includes(value)
}

/**
 * Connectors a Connector step can pick.
 *
 * Disabled connectors are left out (the run would refuse them), and so is a
 * database connector that belongs to someone else: only its owner may query
 * it, so a step by `userId` could never run it. The connector the step
 * already points at always stays in the list: by name when it is among
 * `connectors`, by its id when it is not (still loading, deleted, or no
 * longer shared with this user), so the field never shows up empty while the
 * step still holds a reference.
 */
export function connectorOptions(
  connectors: ConnectorResponse[],
  selectedId: string,
  userId: string | null,
): SelectOption[] {
  const options = connectors
    .filter(
      (c) =>
        c.id === selectedId || (c.is_active && (c.type !== "database" || c.user_id === userId)),
    )
    .map((c) => ({ value: c.id, label: c.name }))
  if (selectedId && !options.some((o) => o.value === selectedId)) {
    options.push({ value: selectedId, label: selectedId })
  }
  return options
}

/**
 * Whether the step's connector is a database. Known from the connector once
 * the list has loaded; until then, from what the step stores as its action.
 */
export function isDatabaseStep(
  connectors: ConnectorResponse[],
  connectorId: string,
  actionId: string,
): boolean {
  const connector = connectors.find((c) => c.id === connectorId)
  return connector ? connector.type === "database" : isDatabaseOperation(actionId)
}

/**
 * The parameters a database operation takes, keeping what the step already
 * holds for it: `sql` for a query, `table_name` to describe a table, none
 * to list tables.
 */
export function databaseParameters(
  operation: string,
  current: Record<string, unknown>,
): Record<string, string> {
  const text = (key: string) => (typeof current[key] === "string" ? (current[key] as string) : "")
  if (operation === "query") return { sql: text("sql") }
  if (operation === "describe_table") return { table_name: text("table_name") }
  return {}
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

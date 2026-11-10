/**
 * The arguments of an MCP tool, as the step editor shows and stores them.
 *
 * A tool describes its arguments with a JSON schema. The editor shows one
 * field for each property of that schema and stores what is typed under
 * `parameters`, in the type the schema asks for. A value that contains a
 * `{{variable}}` reference is stored as the text typed: the run fills the
 * reference in and gives the result the schema's type.
 */

/** A tool of an MCP server, as the step-options listing returns it. */
export interface McpStepTool {
  name: string
  description: string
  input_schema: Record<string, unknown> | null
}

export type ArgumentType = "string" | "number" | "integer" | "boolean" | "object" | "array" | "any"

export interface ArgumentField {
  name: string
  type: ArgumentType
  description: string
  required: boolean
  /** The allowed values, when the schema lists them. */
  options: string[] | null
}

const KNOWN_TYPES: readonly string[] = ["string", "number", "integer", "boolean", "object", "array"]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function typeOf(property: Record<string, unknown>): ArgumentType {
  const declared = property.type
  // `["string", "null"]`: the first type that is not null.
  const name = Array.isArray(declared) ? declared.find((t) => t !== "null") : declared
  return typeof name === "string" && KNOWN_TYPES.includes(name) ? (name as ArgumentType) : "any"
}

/**
 * One field for each property of a tool's input schema, required ones
 * first. `null` when the schema is not an object with named properties: the
 * editor then cannot offer fields and falls back to JSON. A tool that takes
 * no arguments gives an empty list.
 */
export function argumentFields(schema: unknown): ArgumentField[] | null {
  if (!isRecord(schema)) return null
  const properties = schema.properties
  if (properties === undefined) {
    // `{type: "object"}` with nothing else: a tool without arguments.
    return schema.type === "object" && schema.additionalProperties === undefined ? [] : null
  }
  if (!isRecord(properties)) return null
  const required = Array.isArray(schema.required) ? schema.required : []
  const fields = Object.entries(properties).map(([name, raw]): ArgumentField => {
    const property = isRecord(raw) ? raw : {}
    const options = Array.isArray(property.enum)
      ? property.enum.filter((v) => typeof v === "string" || typeof v === "number").map(String)
      : null
    return {
      name,
      type: typeOf(property),
      description: typeof property.description === "string" ? property.description : "",
      required: required.includes(name),
      options: options && options.length > 0 ? options : null,
    }
  })
  return [...fields.filter((f) => f.required), ...fields.filter((f) => !f.required)]
}

/** What a field shows for a stored value. */
export function toArgumentText(value: unknown): string {
  if (value === undefined || value === null) return ""
  if (typeof value === "string") return value
  return JSON.stringify(value)
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

/**
 * What is stored for the text typed into a field of `type`. `undefined`
 * means "not set" (the argument is left out). Text that is not yet a value
 * of the type (a number half typed, JSON not closed) is kept as text, so
 * typing is never undone; the tool reports it if it is run that way.
 */
export function fromArgumentText(text: string, type: ArgumentType): unknown {
  if (text === "") return undefined
  if (type === "string" || text.includes("{{")) return text
  const trimmed = text.trim()
  if (type === "number" || type === "integer") {
    const n = Number(trimmed)
    const fits = trimmed !== "" && Number.isFinite(n) && (type === "number" || Number.isInteger(n))
    // "1." and "1.0" are numbers still being typed: keep the text.
    return fits && String(n) === trimmed ? n : text
  }
  if (type === "boolean") {
    return trimmed === "true" ? true : trimmed === "false" ? false : text
  }
  const parsed = parseJson(trimmed)
  if (type === "object") return isRecord(parsed) ? parsed : text
  if (type === "array") return Array.isArray(parsed) ? parsed : text
  // No declared type: JSON when it is JSON, else the text.
  return parsed === undefined ? text : parsed
}

/** `parameters` with one argument set, or removed when `value` is undefined. */
export function withArgument(
  parameters: Record<string, unknown>,
  name: string,
  value: unknown,
): Record<string, unknown> {
  const next = { ...parameters }
  if (value === undefined) delete next[name]
  else next[name] = value
  return next
}

/** The required arguments the step does not set yet. */
export function missingArguments(
  fields: ArgumentField[],
  parameters: Record<string, unknown>,
): string[] {
  return fields
    .filter((f) => f.required && (parameters[f.name] === undefined || parameters[f.name] === ""))
    .map((f) => f.name)
}

/** The whole `parameters` object as the JSON editor shows it. */
export function toArgumentsJson(parameters: Record<string, unknown>): string {
  return Object.keys(parameters).length === 0 ? "" : JSON.stringify(parameters, null, 2)
}

/**
 * The `parameters` object for what is typed into the JSON editor, or `null`
 * when it is not a JSON object. Nothing typed is no arguments.
 */
export function parseArgumentsJson(text: string): Record<string, unknown> | null {
  if (text.trim() === "") return {}
  const parsed = parseJson(text)
  return isRecord(parsed) ? parsed : null
}

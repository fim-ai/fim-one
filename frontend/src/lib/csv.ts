/**
 * One cell of a CSV file a spreadsheet will not run.
 *
 * A spreadsheet program reads a cell that starts with = + - @, a tab or a
 * carriage return as a formula. The tables exported here are model output,
 * which can quote text from anywhere, so such a cell gets an apostrophe in
 * front (the program then shows it as text). A plain signed number stays a
 * number.
 */
const FORMULA_START = /^[=+\-@\t\r]/
const PLAIN_NUMBER = /^[+-]?\d+(?:[.,]\d+)*%?$/

export function csvCell(cell: string): string {
  const safe = FORMULA_START.test(cell) && !PLAIN_NUMBER.test(cell) ? `'${cell}` : cell
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe
}

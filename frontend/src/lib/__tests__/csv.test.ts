import { describe, expect, it } from "vitest"

import { csvCell } from "@/lib/csv"

describe("csvCell", () => {
  it("puts an apostrophe before text a spreadsheet would run", () => {
    expect(csvCell("=HYPERLINK(\"http://x\",\"y\")")).toBe("\"'=HYPERLINK(\"\"http://x\"\",\"\"y\"\")\"")
    expect(csvCell("+cmd|' /C calc'!A0")).toBe("'+cmd|' /C calc'!A0")
    expect(csvCell("@SUM(1+1)")).toBe("'@SUM(1+1)")
    expect(csvCell("-2+3")).toBe("'-2+3")
    expect(csvCell("\tx")).toBe("'\tx")
  })

  it("leaves numbers and ordinary text as they are", () => {
    expect(csvCell("-5")).toBe("-5")
    expect(csvCell("+1,234.50")).toBe("\"+1,234.50\"")
    expect(csvCell("-12%")).toBe("-12%")
    expect(csvCell("plain")).toBe("plain")
    expect(csvCell("a = b")).toBe("a = b")
  })

  it("quotes commas, quotes and line breaks", () => {
    expect(csvCell("a,b")).toBe("\"a,b\"")
    expect(csvCell("say \"hi\"")).toBe("\"say \"\"hi\"\"\"")
    expect(csvCell("one\ntwo")).toBe("\"one\ntwo\"")
  })
})

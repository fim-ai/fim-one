#!/usr/bin/env node
/**
 * A one-tool MCP server over stdio for the workflow smoke: newline-delimited
 * JSON-RPC, no dependencies. The tool `echo` returns its `text` argument
 * prefixed with `MCP_STUB`.
 */
import readline from "node:readline"

const TOOL = {
  name: "echo",
  description: "Return the given text.",
  inputSchema: {
    type: "object",
    properties: { text: { type: "string" } },
  },
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`)
}

function handle(message) {
  const { id, method, params } = message
  // Notifications carry no id and get no reply.
  if (id === undefined || id === null) return
  switch (method) {
    case "initialize":
      return send({
        jsonrpc: "2.0",
        id,
        result: {
          protocolVersion: params?.protocolVersion ?? "2024-11-05",
          capabilities: { tools: {} },
          serverInfo: { name: "e2e-stub", version: "0.0.0" },
        },
      })
    case "ping":
      return send({ jsonrpc: "2.0", id, result: {} })
    case "tools/list":
      return send({ jsonrpc: "2.0", id, result: { tools: [TOOL] } })
    case "tools/call": {
      const text = typeof params?.arguments?.text === "string" ? params.arguments.text : ""
      return send({
        jsonrpc: "2.0",
        id,
        result: { content: [{ type: "text", text: `MCP_STUB ${text}`.trim() }], isError: false },
      })
    }
    default:
      return send({
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Method not found: ${method}` },
      })
  }
}

const rl = readline.createInterface({ input: process.stdin })
rl.on("line", (line) => {
  const trimmed = line.trim()
  if (!trimmed) return
  try {
    handle(JSON.parse(trimmed))
  } catch {
    // Not JSON: ignore, as a real server would log and move on.
  }
})
rl.on("close", () => process.exit(0))

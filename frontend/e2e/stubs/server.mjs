#!/usr/bin/env node
/**
 * Stand-ins for everything the workflow smoke would otherwise reach over the
 * network. One HTTP server, no dependencies:
 *
 *   POST /v1/chat/completions   OpenAI-compatible model (stream and non-stream)
 *   POST /v1/embeddings         OpenAI-compatible embeddings (fixed vectors)
 *   GET  /connector/echo        target of the connector the smoke creates
 *   GET  /__requests            what was called so far (for assertions)
 *   GET  /health
 *
 * The model answers every prompt with `STUB_REPLY` plus the last user message,
 * so a test can tell which prompt produced which output.
 */
import http from "node:http"

const port = Number(process.env.E2E_STUB_PORT || 8766)

/** @type {{ path: string, model?: string, stream?: boolean, lastUser?: string }[]} */
const requests = []

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ""
    req.on("data", (chunk) => (raw += chunk))
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch {
        resolve({})
      }
    })
  })
}

function textOf(content) {
  if (typeof content === "string") return content
  if (Array.isArray(content)) {
    return content.map((part) => (typeof part?.text === "string" ? part.text : "")).join("")
  }
  return ""
}

function json(res, status, body) {
  const data = JSON.stringify(body)
  res.writeHead(status, { "content-type": "application/json" })
  res.end(data)
}

/** The reply for a chat request: plain text, or the JSON a ReAct loop asks for. */
function replyFor(body) {
  const messages = Array.isArray(body.messages) ? body.messages : []
  const lastUser = [...messages].reverse().find((m) => m.role === "user")
  const system = messages.find((m) => m.role === "system")
  const answer = `STUB_REPLY ${textOf(lastUser?.content).slice(0, 200)}`.trim()
  // A JSON-mode agent loop expects an action object; hand it a final answer.
  const wantsAction =
    body.response_format?.type === "json_object" ||
    body.response_format?.type === "json_schema" ||
    textOf(system?.content).includes('"final_answer"')
  if (wantsAction && !Array.isArray(body.tools)) {
    return JSON.stringify({ type: "final_answer", reasoning: "stub", answer })
  }
  return answer
}

const usage = { prompt_tokens: 7, completion_tokens: 5, total_tokens: 12 }

async function chatCompletions(req, res) {
  const body = await readBody(req)
  const messages = Array.isArray(body.messages) ? body.messages : []
  const lastUser = [...messages].reverse().find((m) => m.role === "user")
  requests.push({
    path: "/v1/chat/completions",
    model: body.model,
    stream: body.stream === true,
    lastUser: textOf(lastUser?.content).slice(0, 200),
  })
  const content = replyFor(body)
  const id = `chatcmpl-stub-${requests.length}`
  const created = Math.floor(Date.now() / 1000)
  const model = body.model || "stub-model"

  if (body.stream === true) {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    })
    const chunk = (delta, finish, extra = {}) =>
      res.write(
        `data: ${JSON.stringify({
          id,
          object: "chat.completion.chunk",
          created,
          model,
          choices: [{ index: 0, delta, finish_reason: finish }],
          ...extra,
        })}\n\n`,
      )
    chunk({ role: "assistant", content: "" }, null)
    chunk({ content }, null)
    chunk({}, "stop")
    res.write(
      `data: ${JSON.stringify({ id, object: "chat.completion.chunk", created, model, choices: [], usage })}\n\n`,
    )
    res.write("data: [DONE]\n\n")
    res.end()
    return
  }

  json(res, 200, {
    id,
    object: "chat.completion",
    created,
    model,
    choices: [{ index: 0, message: { role: "assistant", content }, finish_reason: "stop" }],
    usage,
  })
}

async function embeddings(req, res) {
  const body = await readBody(req)
  const input = Array.isArray(body.input) ? body.input : [body.input ?? ""]
  requests.push({ path: "/v1/embeddings", model: body.model })
  const dim = Number(body.dimensions) || 8
  json(res, 200, {
    object: "list",
    model: body.model || "stub-embedding",
    data: input.map((_, index) => ({
      object: "embedding",
      index,
      embedding: Array.from({ length: dim }, (_, i) => (i === 0 ? 1 : 0)),
    })),
    usage: { prompt_tokens: input.length, total_tokens: input.length },
  })
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`)
  try {
    if (url.pathname === "/health") return json(res, 200, { ok: true })
    if (url.pathname === "/__requests") return json(res, 200, { requests })
    if (req.method === "POST" && url.pathname === "/v1/chat/completions") {
      return await chatCompletions(req, res)
    }
    if (req.method === "POST" && url.pathname === "/v1/embeddings") {
      return await embeddings(req, res)
    }
    if (url.pathname === "/v1/models") {
      return json(res, 200, { object: "list", data: [{ id: "stub-model", object: "model" }] })
    }
    if (url.pathname === "/connector/echo") {
      requests.push({ path: "/connector/echo" })
      return json(res, 200, {
        stub: "connector",
        echo: Object.fromEntries(url.searchParams.entries()),
      })
    }
    json(res, 404, { error: { message: `stub has no route for ${req.method} ${url.pathname}` } })
  } catch (err) {
    json(res, 500, { error: { message: String(err) } })
  }
})

server.listen(port, "127.0.0.1", () => {
  console.log(`[e2e stub] listening on http://127.0.0.1:${port}`)
})

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)))
}

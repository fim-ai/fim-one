import { expect, test, type APIRequestContext } from "@playwright/test"

import { adminSession, signIn } from "./helpers"

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8765"

async function call<T>(
  request: APIRequestContext,
  token: string,
  method: "POST" | "PUT",
  path: string,
  data: unknown,
): Promise<T> {
  const res = await request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok(), `${method} ${path} -> ${res.status()} ${await res.text()}`).toBe(true)
  return (await res.json()) as T
}

// A conversation whose agent is no longer available (unpublished,
// unsubscribed or switched off) is refused with 409 agent_unavailable. The
// composer showed the message as sent and nothing else.
test("a message refused because the conversation's agent is unavailable says why", async ({
  page,
  request,
}) => {
  const session = await adminSession(request)
  const token = session.accessToken
  const agent = await call<{ data: { id: string } }>(request, token, "POST", "/api/agents", {
    name: "Soon switched off",
    instructions: "Answer briefly.",
  })
  const conversation = await call<{ id?: string; data?: { id: string } }>(
    request,
    token,
    "POST",
    "/api/conversations",
    { mode: "react", title: "Old conversation", agent_id: agent.data.id },
  )
  const conversationId = conversation.data?.id ?? conversation.id
  await call(request, token, "PUT", `/api/agents/${agent.data.id}`, { is_active: false })

  await signIn(page, session)
  await page.goto(`/?c=${conversationId}`)
  const composer = page.locator("textarea").first()
  await expect(composer).toBeVisible()
  const refused = page.waitForResponse(
    (r) => r.url().endsWith("/api/react") && r.request().method() === "POST",
  )
  await composer.fill("Are you still there?")
  await composer.press("Enter")
  expect((await refused).status()).toBe(409)
  await expect(page.getByText(/The agent this conversation uses is no longer available to you/)).toBeVisible()
})

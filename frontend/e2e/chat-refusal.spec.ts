import { expect, test, type APIRequestContext } from "@playwright/test"

import { adminSession, signIn } from "./helpers"

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8765"
const MARKET_ORG_ID = "00000000-0000-0000-0000-000000000001"

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
  await expect(page.getByTestId("turn-refusal")).toContainText(
    "The agent this conversation uses is no longer available to you",
  )
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0)
})

// As it happened in testing: A publishes an agent, B subscribes and chats
// with it, A unpublishes, B writes again in that conversation. The reason
// stays under B's message (a toast is gone after four seconds), and there
// is no Retry: retrying is refused the same way.
test("B's message after A unpublished the agent keeps saying why, with no Retry", async ({
  page,
  request,
}) => {
  const a = await adminSession(request)
  const agent = await call<{ data: { id: string } }>(request, a.accessToken, "POST", "/api/agents", {
    name: "Published then withdrawn",
    instructions: "Answer briefly.",
  })
  await call(request, a.accessToken, "POST", `/api/agents/${agent.data.id}/publish`, {
    scope: "org",
    org_id: MARKET_ORG_ID,
  })
  // The Market reviews what is published to it; A is its administrator.
  await call(request, a.accessToken, "POST", "/api/admin/reviews/batch-approve", {
    items: [{ resource_type: "agent", resource_id: agent.data.id }],
  })

  const registered = await request.post(`${API}/api/auth/register`, {
    data: { email: "b@example.com", password: "smoke-password-2", privacy_accepted: true },
  })
  expect(registered.ok(), `register -> ${registered.status()} ${await registered.text()}`).toBe(true)
  const b = (await registered.json()) as {
    access_token: string
    refresh_token: string
    user: Record<string, unknown>
  }
  await call(request, b.access_token, "POST", "/api/market/subscribe", {
    resource_type: "agent",
    resource_id: agent.data.id,
  })
  const conversation = await call<{ id?: string; data?: { id: string } }>(
    request,
    b.access_token,
    "POST",
    "/api/conversations",
    { mode: "react", title: "B and the agent", agent_id: agent.data.id },
  )
  const conversationId = conversation.data?.id ?? conversation.id

  await signIn(page, { accessToken: b.access_token, refreshToken: b.refresh_token, user: b.user })
  await page.goto(`/?c=${conversationId}`)
  const composer = page.locator("textarea").first()
  await expect(composer).toBeVisible()

  // B chats once while the agent is available.
  const answered = page.waitForResponse(
    (r) => r.url().endsWith("/api/react") && r.request().method() === "POST",
  )
  await composer.fill("Hello there")
  await composer.press("Enter")
  expect((await answered).status()).toBe(200)
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0)
  await expect(composer).toBeEnabled()
  await page.waitForTimeout(1500)

  await call(request, a.accessToken, "POST", `/api/agents/${agent.data.id}/unpublish`, {})

  const refused = page.waitForResponse(
    (r) => r.url().endsWith("/api/react") && r.request().method() === "POST",
  )
  await composer.fill("Are you still there?")
  await composer.press("Enter")
  expect((await refused).status()).toBe(409)
  const note = page.getByTestId("turn-refusal")
  await expect(note).toContainText("The agent this conversation uses is no longer available to you")
  await page.waitForTimeout(10_000)
  await expect(note).toBeVisible()
  await expect(note).toContainText("no longer available")
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0)
})

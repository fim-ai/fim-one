import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import channels from "../../../../messages/en/channels.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { ChannelFormDialog } from "@/components/channels/channel-form-dialog"
import { ApiError } from "@/lib/api"
import type { Channel } from "@/types/channel"

const api = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), discoverChats: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api/channels", () => ({ channelsApi: api }))
vi.mock("sonner", () => ({ toast }))

const channel = (over: Partial<Channel>): Channel => ({
  id: "ch1",
  name: "Ops",
  type: "slack_webhook",
  org_id: "org1",
  config: { webhook_url: "***" },
  callback_url: "/api/channels/ch1/callback",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...over,
})

function open(editing: Channel | null) {
  const onSaved = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={{ channels, common, errors }} timeZone="UTC">
      <ChannelFormDialog open onOpenChange={() => {}} channel={editing} orgId="org1" onSaved={onSaved} />
    </NextIntlClientProvider>,
  )
  return onSaved
}

const submit = () => fireEvent.click(screen.getByRole("button", { name: "Save" }))

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub)
})

beforeEach(() => {
  vi.clearAllMocks()
  api.update.mockImplementation(async (_id: string, body: { name: string }) => channel({ name: body.name }))
})

afterEach(cleanup)

describe("ChannelFormDialog, webhook channel", () => {
  it("shows the webhook field and none of the Feishu app fields", () => {
    open(channel({}))
    expect(screen.getByLabelText(/Webhook URL/)).toBeTruthy()
    expect(screen.queryByLabelText(/App ID/)).toBeNull()
    expect(screen.queryByLabelText(/Encrypt Key/)).toBeNull()
  })

  it("keeps the stored URL when the field is left blank", async () => {
    open(channel({}))
    fireEvent.change(screen.getByLabelText(/Channel Name/), { target: { value: "Ops 2" } })
    submit()
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1))
    expect(api.update).toHaveBeenCalledWith("ch1", { name: "Ops 2", config: {} })
  })

  it("refuses a URL on another host before calling the API", async () => {
    open(channel({}))
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: "https://example.com/hook" },
    })
    submit()
    expect(await screen.findByText(channels.form.webhook_url_invalid)).toBeTruthy()
    expect(api.update).not.toHaveBeenCalled()
  })

  it("sends a new URL on the platform's host", async () => {
    open(channel({}))
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: "https://hooks.slack.com/services/T/B/new" },
    })
    submit()
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1))
    expect(api.update.mock.calls[0][1].config).toEqual({
      webhook_url: "https://hooks.slack.com/services/T/B/new",
    })
  })
})

describe("ChannelFormDialog, email channel", () => {
  const email = channel({ type: "email", config: { to: "ops@corp.internal" } })

  it("requires a recipient address", async () => {
    open(email)
    fireEvent.change(screen.getByLabelText(/Recipient addresses/), { target: { value: "nobody" } })
    submit()
    expect(await screen.findByText(channels.form.email_to_required)).toBeTruthy()
    expect(api.update).not.toHaveBeenCalled()
  })

  it("shows the server's refusal under the recipients field", async () => {
    api.update.mockRejectedValue(
      new ApiError(400, "channel_email_not_permitted", "channel_email_not_permitted", {}),
    )
    open(email)
    fireEvent.change(screen.getByLabelText(/Recipient addresses/), {
      target: { value: "anyone@outside.io" },
    })
    submit()
    expect(await screen.findByText(errors.channel_email_not_permitted)).toBeTruthy()
    expect(api.update).toHaveBeenCalledWith("ch1", { name: "Ops", config: { to: "anyone@outside.io" } })
    expect(toast.error).not.toHaveBeenCalled()
  })
})

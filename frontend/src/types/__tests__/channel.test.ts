import { describe, expect, it } from "vitest"

import { isWebhookChannelType, isWebhookUrlFor } from "@/types/channel"

describe("isWebhookChannelType", () => {
  it("names the three webhook types and nothing else", () => {
    expect(isWebhookChannelType("slack_webhook")).toBe(true)
    expect(isWebhookChannelType("lark_webhook")).toBe(true)
    expect(isWebhookChannelType("wecom_webhook")).toBe(true)
    expect(isWebhookChannelType("feishu")).toBe(false)
    expect(isWebhookChannelType("email")).toBe(false)
  })
})

describe("isWebhookUrlFor", () => {
  it("accepts the platform's own https host", () => {
    expect(isWebhookUrlFor("slack_webhook", "https://hooks.slack.com/services/T/B/x")).toBe(true)
    expect(isWebhookUrlFor("lark_webhook", "https://open.larksuite.com/open-apis/bot/v2/hook/x")).toBe(true)
    expect(isWebhookUrlFor("wecom_webhook", " https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x ")).toBe(true)
  })

  it.each([
    "",
    "not a url",
    "http://hooks.slack.com/services/T/B/x",
    "https://hooks.slack.com.evil.io/services/T/B/x",
    "https://user@hooks.slack.com/services/T/B/x",
    "https://hooks.slack.com:8443/services/T/B/x",
    "https://hooks.slack.com/",
    "https://open.feishu.cn/open-apis/bot/v2/hook/x",
  ])("refuses %s for Slack", (url) => {
    expect(isWebhookUrlFor("slack_webhook", url)).toBe(false)
  })
})

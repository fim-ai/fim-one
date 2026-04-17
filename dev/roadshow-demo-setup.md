# 东升杯 Roadshow — Feishu Gate Demo Setup

This guide describes the exact configuration needed to run the "Feishu
confirmation gate" segment of the 2026-04-24 roadshow demo. The punchline
is: when the agent tries to call the `oa__purchase_pay` tool, a rich card
pops up in the Feishu group; the presenter taps **Approve**, and the tool
proceeds. Tap **Reject** and the tool is blocked.

Everything below assumes FIM One is running at `https://one.fim.ai`
(or whatever `BACKEND_URL` you point Feishu at).

---

## 1. Pre-filled demo Channel credentials

These values are the ones the team has already provisioned. Use them as-is
when you reach the "Create Channel" step further down.

| Field | Value |
|---|---|
| App ID | `cli_a9271aab36f89bb4` |
| App Secret | `P6TzXl0kd52yQt7s4JSqMbNkGqZm4hif` |
| Chat ID | `oc_4f313b3d41ae30fbcb5a23710096982e` |
| Chat Name | `FIM One` |
| Tenant Key | `10b87b7f1897575f` |
| Owner Open ID | `ou_fd960776ebfe136028a3fea1c9257ef9` |

> The demo sends the card to the **group chat** (Chat ID above).
> Any member of that group can tap Approve / Reject. Individual DM routing
> (per-user open_id) is deferred to v0.9 and is not used here.

---

## 2. Feishu open-platform setup

Only do this once per app — the values above are already configured.

1. Go to <https://open.feishu.cn/app/> → sign in → open the app
   `cli_a9271aab36f89bb4`.
2. Under **Credentials & Basic Info**, confirm the App Secret matches.
3. Under **Permissions & Scopes**, make sure the following are granted:
   - `im:message` (send messages)
   - `im:message:send_as_bot`
   - `im:chat` (read/write group chats)
   - `im:chat:readonly` (at minimum)
4. Under **Event Subscriptions** → **Request URL**, set:
   ```
   https://one.fim.ai/api/channels/<CHANNEL_ID>/callback
   ```
   (you get `<CHANNEL_ID>` from step 4 below). Click **Verify** — Feishu
   will POST `{"type":"url_verification","challenge":"..."}` to the URL,
   and our callback will echo `challenge` back.
5. Under **Bot** → **Features**, enable **Message Card Actions**. This
   ensures clicks on our Approve/Reject buttons are delivered to the
   same Request URL.
6. (Optional but recommended) Under **Event Subscriptions** →
   **Encrypt Strategy**, set a **Verification Token** and an
   **Encrypt Key**. Copy both into the Channel's `config` when you
   create it below — our `verify_signature()` uses the Encrypt Key to
   validate every callback.
7. Add the bot to the `FIM One` group chat (`oc_4f313b...`).

---

## 3. Mock data store

### 3.1 MySQL — historical purchase data

Drop this SQL against any local MySQL (the demo uses `demo_oa`):

```sql
CREATE DATABASE IF NOT EXISTS demo_oa CHARACTER SET utf8mb4;
USE demo_oa;

CREATE TABLE IF NOT EXISTS purchase_history (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    vendor       VARCHAR(100) NOT NULL,
    item         VARCHAR(200) NOT NULL,
    amount_cny   DECIMAL(12,2) NOT NULL,
    ordered_at   DATETIME NOT NULL,
    status       VARCHAR(20) NOT NULL
);

INSERT INTO purchase_history (vendor, item, amount_cny, ordered_at, status) VALUES
 ('北京智成科技', '高性能服务器 DL380', 128000.00, '2026-02-14 10:22:00', 'paid'),
 ('北京智成科技', 'SSD 存储 3.84TB x 8', 65200.00, '2026-03-02 14:05:00', 'paid'),
 ('上海卓越耗材', '办公耗材季度套餐',  18200.00, '2026-03-18 09:41:00', 'paid'),
 ('深圳领航数据', '内部审计数据服务',  58000.00, '2026-03-25 16:12:00', 'paid'),
 ('北京智成科技', 'GPU A100 x 2',     240000.00, '2026-04-05 11:08:00', 'pending_approval');
```

Register it in FIM One:

1. Portal → **Connectors** → **New database connector**.
2. Host / port / db / user / password of your local MySQL.
3. Name it `oa_mysql`. Click **Introspect schema** → confirm
   `purchase_history` is listed.
4. Save.

### 3.2 Feishu Doc — contract draft

Either create a real Feishu Doc at
<https://hzn5uy7d2w.feishu.cn/docx/DemoContractDraft> and paste in a
one-page boilerplate contract, **or** mock it by adding a markdown file
to `uploads/kb/demo-contracts/contract-2026-04.md` and attaching that KB
to the demo agent. The agent's job is only to _read_ the doc title and
line items, so either works.

### 3.3 OA HTTP webhook — the "pay" tool

Run a trivial webhook receiver locally:

```python
# scripts/demo_oa_webhook.py
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/oa/purchase/pay")
async def pay(req: Request):
    body = await req.json()
    print("Mock OA received pay request:", body)
    return {"ok": True, "po_id": "PO-2026-0418"}
```

Run it with `uvicorn scripts.demo_oa_webhook:app --port 7799`.

Then in FIM One:

1. Portal → **Connectors** → **New HTTP connector**.
2. Base URL: `http://localhost:7799`.
3. Add an action:
   - Name: `purchase_pay`
   - Method: `POST`
   - Path: `/oa/purchase/pay`
   - `requires_confirmation`: **on** — this is what triggers
     `FeishuGateHook`.
   - Parameters schema:
     ```json
     {
       "type":"object",
       "properties":{
         "vendor":{"type":"string"},
         "item":{"type":"string"},
         "amount_cny":{"type":"number"}
       },
       "required":["vendor","item","amount_cny"]
     }
     ```

---

## 4. Create the Feishu Channel in FIM One

1. Sign into the portal as an **Org Owner** (or Admin) of the org you are
   demoing.
2. Portal → **Organization settings** → **Channels** → **New channel**.
   (If the UI isn't wired yet, `POST /api/channels` with:
   ```json
   {
     "name": "FIM One Demo Feishu",
     "type": "feishu",
     "org_id": "<YOUR_ORG_ID>",
     "is_active": true,
     "config": {
       "app_id": "cli_a9271aab36f89bb4",
       "app_secret": "P6TzXl0kd52yQt7s4JSqMbNkGqZm4hif",
       "chat_id": "oc_4f313b3d41ae30fbcb5a23710096982e",
       "verification_token": "<from Feishu console>",
       "encrypt_key": "<from Feishu console>"
     }
   }
   ```
   The response contains `id` (the `<CHANNEL_ID>`) and `callback_url`.)
3. Copy the `callback_url` from the response — paste it into the Feishu
   app's **Event Subscriptions → Request URL**. Click **Verify**.
4. Back in the portal, click **Send test message**. Check the FIM One
   group — you should see a plaintext `FIM One test message from
   <your email>`. If so, the channel is live.

---

## 5. Demo agent configuration

Portal → **Agents** → **New agent** → fill:

- **Name**: `采购审批助手 Procurement Assistant`
- **Avatar**: anything
- **System prompt** (copy-paste):
  ```
  你是 FIM One 的采购审批助手。当用户询问历史采购记录时，从
  oa_mysql 的 purchase_history 表查询。当用户要求执行一笔支付时，
  先用 oa_mysql 验证该供应商过去一年内的累计采购金额，再调用
  oa_http.purchase_pay 提交付款。所有付款动作都需要人工审批。
  回答必须简洁，用中文。
  ```
- **Tools / Connectors**: add `oa_mysql` and `oa_http` (with
  `purchase_pay`).
- **Hooks**: enable `feishu_gate`. (If the agent UI doesn't expose it
  yet, set `model_config_json.hooks = {"class_hooks": ["feishu_gate"]}`
  via `PATCH /api/agents/{id}` or via the agent JSON editor.)
- Save.

---

## 6. One-click smoke test

Run this from the repo root after everything above is wired up:

```bash
# Prereqs:
#   1. ./start.sh has the backend running
#   2. scripts/demo_oa_webhook.py is running on :7799
#   3. The Feishu channel was created and test-sent successfully
#
# Replace <AGENT_ID> and <AUTH_TOKEN> with values for your org.

curl -N -X POST https://one.fim.ai/api/react \
  -H "Authorization: Bearer <AUTH_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<AGENT_ID>",
    "message": "请帮我给北京智成科技支付 GPU A100 两张的尾款 240000 元"
  }'
```

You should observe, in order:

1. The agent issues a MySQL query against `purchase_history` and
   summarises the vendor's recent spend.
2. Before the `purchase_pay` call fires, a Feishu interactive card
   appears in the `FIM One` group with **Approve** / **Reject**.
3. The SSE stream shows the agent waiting.
4. Tap **Approve** — the SSE stream resumes; the mock webhook prints
   the received payload; the agent announces success.
5. Re-run the same curl and tap **Reject** this time — the agent
   reports the tool was blocked by an operator.

---

## 7. Day-of presenter cheat sheet (5 steps)

Show these on stage / print them out:

1. `./start.sh` on the demo laptop → backend up at
   `https://one.fim.ai`.
2. `uvicorn scripts.demo_oa_webhook:app --port 7799` in a second
   terminal → mock OA up.
3. Sign into portal, open agent `采购审批助手`, click **Chat**.
4. Type the rehearsed prompt (section 6 above). Watch the Feishu group
   on a second screen for the card to appear.
5. Tap **Approve** on the Feishu card → agent completes. Done.

If the card doesn't show up within 3 s:
- confirm the Channel row has `is_active = true`
- confirm the Request URL in the Feishu console matches the
  `callback_url` returned by the `GET /api/channels/{id}` call
- check `docker logs fim-one-api | grep feishu_gate`

<div align="center">

![FIM One バナー](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![CI](https://github.com/fim-ai/fim-one/actions/workflows/test.yml/badge.svg)](https://github.com/fim-ai/fim-one/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**グローバル × 中国企業向けのオールインワン エージェントプラットフォーム。**  
*すでに運用しているあらゆるシステムを、グローバル SaaS から中国向けスタックまで、1つのエージェントコアで接続します。*

🌐 [ウェブサイト](https://one.fim.ai/) · 📖 [ドキュメント](https://docs.fim.ai) · 📋 [変更履歴](https://docs.fim.ai/changelog) · 🐛 [バグを報告](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ セットアップを省略 — FIM One を Cloud でお試しください。**
> マネージド版を [cloud.fim.ai](https://cloud.fim.ai/) で提供しています — Docker、API キー、設定は不要です。サインインして、数秒でシステムの接続を始められます。_アーリーアクセス中。フィードバックをお待ちしています。_

https://github.com/user-attachments/assets/8a0e1136-ffbc-4950-86bd-b2f8884214af

---

## Overview

Global enterprises run a sprawl of systems that don't talk to each other — ERP, CRM, OA, HR, finance, databases, IM platforms across regions. FIM One is the **all-in-one agent platform** that wires every system you already run into one agent core — global SaaS on one side, the full China stack (Feishu, WeCom, DingTalk, DM, Kingbase, etc.) on the other. One brain. Every system. Global SaaS × China Stack.

| Mode           | What it is                                              | Access                  |
| -------------- | ------------------------------------------------------- | ----------------------- |
| **Standalone** | General-purpose AI assistant — search, code, KB         | Portal                  |
| **Copilot**    | AI embedded in a host system's UI                       | API (embed planned)     |
| **Hub**        | Central AI orchestration across all connected systems   | Portal / API            |

```mermaid
graph LR
    ERP <--> Hub["🧠 FIM One Agent Core"]
    Database <--> Hub
    Lark <--> Hub
    Hub <--> CRM
    Hub <--> OA
    Hub <--> API[Custom API]
```

### スクリーンショット

**ダッシュボード** — 統計情報、アクティビティトレンド、トークン使用量、およびエージェントと会話への高速アクセス。

![Dashboard](./assets/screenshot-dashboard.png)

**エージェント チャット** — 接続されたデータベースに対する複数ステップのツール呼び出しを伴う ReAct 推論。

![Agent Chat](./assets/screenshot-agent-chat.png)

**DAG プランナー** — LLM生成の実行計画、並列ステップ、およびライブステータス追跡。

![DAG Planner](./assets/screenshot-dag-planner.png)

### デモ

**エージェントの使用**

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

**プランナーモードの使用**

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## クイックスタート

### Docker（推奨）

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

docker compose up --build -d
```

http://localhost:3000 を開いてください — 初回起動時に管理者アカウントを作成します。以上です。

```bash
docker compose up -d          # start
docker compose down           # stop
docker compose logs -f        # view logs
```

### ローカル開発

前提条件: Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+、pnpm。

```bash
git clone https://github.com/fim-ai/fim-one.git && cd fim-one

cp example.env .env           # Edit: set LLM_API_KEY

uv sync --all-extras
cd frontend && pnpm install && cd ..

./start.sh dev                # hot reload: Python --reload + Next.js HMR
```

| コマンド          | 起動内容                       | URL                            |
| ---------------- | --------------------------------- | ------------------------------ |
| `./start.sh`         | Next.js + FastAPI                 | localhost:3000 (UI) + :8000    |
| `./start.sh dev`     | 同上、ホットリロード付き             | 同上                           |
| `./start.sh dev:api` | API のみ、開発モード (ホットリロード)   | localhost:8000                 |
| `./start.sh dev:ui`  | フロントエンドのみ、開発モード (HMR)    | localhost:3000                 |
| `./start.sh api`     | FastAPI のみ (ヘッドレス)           | localhost:8000/api             |

> 本番環境へのデプロイ (Docker、リバースプロキシ、ゼロダウンタイム更新) については、[デプロイメントガイド](https://docs.fim.ai/quickstart#production-deployment)を参照してください。

## 主な機能

#### 国境を越えた接続性
- **3つの提供形態** — スタンドアロンアシスタント、組み込み型Copilot、または中央Hub。同じエージェントコアを使用します。
- **あらゆるシステムに、1つのパターン** — API、データベース、MCPサーバーを接続できます。アクションは認証情報の注入に対応したエージェントツールとして自動登録されます。段階的開示メタツールにより、あらゆるツールタイプでのtoken使用量を80%以上削減できます。
- **データベースコネクター** — PostgreSQL、MySQL、および中国で一般的なエンタープライズデータベース（DM、KingbaseES、HighGo）に対応します。これらは多くのグローバルプラットフォームからは接続できません。スキーマのイントロスペクションとAIによるアノテーションにも対応しています。
- **構築方法は3つ** — OpenAPI仕様をインポートする、AIチャットビルダーを使用する、またはMCPサーバーに直接接続する。

#### Planning & Execution
- **Dynamic DAG planning** — LLM decomposes goals into dependency graphs at runtime. No hard-coded workflows.
- **Concurrent execution** — Independent steps run in parallel via asyncio; auto re-plan up to 3 rounds in total (the first plan plus 2 re-plans).
- **ReAct agent** — Structured reasoning-and-acting loop with automatic error recovery.
- **Agent harness** — Production-grade execution environment: ContextGuard for 5-layer token-budget management, progressive-disclosure meta-tools to keep the tool surface tractable, and self-reflection loops to counter goal drift.
- **Hook System** — Deterministic enforcement that runs outside the LLM loop. First shipped: `ApprovalGateHook` gates sensitive tool calls behind a human approval card in the chat or in a Feishu group. Extensible to audit logging, read-only-mode guards, and rate limits (v0.9).
- **Content guardrails** — Three-layer safety: tool-permission hooks (actions), credential / SSRF / MCP-auth checks (protocols), and content guardrails (input/output text). Default jailbreak-phrase detector aborts the turn before the LLM is invoked, saving tokens and surfacing a clear blocked notice in chat. Output guardrails optional via `FIM_GUARDRAILS_OUTPUT`.
- **Auto-routing** — Auto mode classifies each query and routes it to ReAct or DAG. Selectable per agent and per message; the default is ReAct.
- **Extended thinking** — Chain-of-thought for OpenAI o-series, Gemini 2.5+, Claude.
- **Prompt-cache observability** — Anthropic prompt-cache `read/create` token counts captured per turn, surfaced in the chat `done` payload and logged so operators can verify cache hits and detect relay stations that don't honor the discount.

#### Workflow & Tools
- **Visual workflow editor** — 9 node types, drag-and-drop canvas (React Flow v12), import/export as JSON. Optional module, off by default; an admin enables it under Admin → Settings → Modules.
- **Smart file handling** — Uploaded files auto-inlined into context (small) or readable on-demand via `read_uploaded_file` tool. Intelligent document processing: PDFs, DOCX, and PPTX files get vision-aware processing with embedded image extraction when the model supports vision. Smart PDF mode extracts text from text-rich pages and renders scanned pages as images.
- **Universal document conversion** — Built-in `convert_to_markdown` tool turns PDF / Word / Excel / PowerPoint / HTML / images / audio / Outlook `.msg` / EPUB / YouTube transcripts into clean Markdown via Microsoft MarkItDown. Vision-capable LLMs OCR embedded images and scanned pages — works with Claude, Gemini, Bedrock, and any LiteLLM-supported provider, no per-provider adapter code.
- **Pluggable tools** — Python, Node.js, shell exec with optional Docker sandbox (`CODE_EXEC_BACKEND=docker`).
- **V4A patch editing** — Beyond `find_replace`, agents can apply line-hunk patches with fuzzy whitespace matching via `file_ops.apply_patch` — robust to multi-line edits where exact-substring match would be brittle.
- **Full RAG pipeline** — Jina embedding + LanceDB + hybrid retrieval + reranker + inline `[N]` citations. Vision-aware ingestion routes scanned PDFs and Office embedded images through the workspace's default vision LLM for OCR.
- **Tool artifacts** — Rich outputs (HTML previews, files) rendered in-chat.

#### Messaging Channels (v0.8)
- **Org-scoped IM bridge** — `BaseChannel` abstraction for outbound messaging across Slack, Microsoft Teams, Discord, Feishu (Lark), WeCom, and DingTalk. First shipping implementation is Feishu; Slack / Teams / WeCom / Email are planned.
- **Fernet-encrypted credentials** — App secrets and encrypt keys encrypted at rest; every inbound callback signature-verified.
- **Interactive approval cards** — Channel-native `GateHook` (Feishu today, Slack/Teams next) posts an Approve / Reject card to your group when a sensitive tool call fires; the tool blocks until a group member taps a verdict. Human-in-the-loop approval without a custom workflow engine.
- **Configurable approval routing per agent** — Three modes (Auto / Inline only / Channel only) with an approver-scope selector (initiator / agent owner / any org member). One audit path stamps `approver_user_id` and `decided_at` whether the verdict came from chat or from the channel. Auto mode falls back to inline if no channel is linked, so agents always get a real approval UX.
- **Task-completion notifications** — Long-running ReAct or DAG agents can push a summary card to the org's channel when work finishes. Configurable per-agent in Settings → Agent → Notifications.
- **Browse-and-pick UI** — No copying raw channel IDs from the vendor console; the portal calls the IM platform's API and shows a group picker.

#### Platform
- **Multi-tenant** — JWT auth, org isolation, admin panel with usage analytics and connector metrics. Multi-worker support via `WORKERS=N` with a Redis interrupt broker for cross-worker relay.
- **Marketplace** — Publish and subscribe to agents, API connectors and MCP servers, plus skills and workflows when those modules are enabled. Knowledge bases and database connectors stay private to their owner.
- **Global skills (SOPs)** — Reusable operating procedures loaded for every user; progressive mode cuts tokens ~80%. Optional module, off by default.
- **Stripe billing & per-user quotas** — Optional Pro-plan upgrade via Stripe Checkout + Customer Portal. Quota chain (per-user override → plan tier → system default) with `0` for unlimited. Admin feature flag gates the entire pipeline; private deployments without Stripe stay clean.
- **Evaluation Center** — Test-dataset management, parallel eval runs with LLM-graded judgments, per-case pass/fail/latency/token results viewer with auto-polling.
- **Conversation recovery** — Synthetic `tool_result` rows persist after interrupted turns; clients auto-reconnect dropped SSE streams via `/chat/resume` with exponential backoff and a "Reconnecting…" indicator.
- **6 languages** — EN, ZH, JA, KO, DE, FR. Translations are [fully automated](https://docs.fim.ai/contributing#i18n) — single glossary drives every LLM translation call (JSON, MDX, README), pre-commit hook refuses manual edits to generated locale files.
- **First-run setup wizard**, dark/light theme, command palette, streaming SSE, DAG visualization.

> Deep dive: [Architecture](https://docs.fim.ai/architecture/system-overview) · [Hook System](https://docs.fim.ai/architecture/hook-system) · [Channels](https://docs.fim.ai/configuration/channels/overview) · [Execution Modes](https://docs.fim.ai/concepts/execution-modes) · [Why FIM One](https://docs.fim.ai/why) · [Competitive Landscape](https://docs.fim.ai/strategy/competitive-landscape)

## Architecture

```mermaid
graph TB
    subgraph app["Application Layer"]
        a["Portal · API · Feishu · Slack · WeCom · Email · Custom Webhooks"]
    end
    subgraph mid["FIM One"]
        direction LR
        m1["Integrations<br/>API / DB / MCP"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m5["Hook System<br/>+ Channels"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems"]
        b["ERP · CRM · OA · Finance · Databases · Contract Mgmt · Custom APIs"]
    end
    app --> mid --> biz
```

Each connector and channel is a standardized bridge — the agent doesn't know or care whether it's talking to SAP, a custom contract system, or a Feishu group. The Hook System runs platform code outside the LLM loop for approvals, audit, and rate limits; Channels carry outbound notifications and approval cards to external IM platforms. See [Connector Architecture](https://docs.fim.ai/architecture/connector-architecture), [Hook System](https://docs.fim.ai/architecture/hook-system), and [Channels](https://docs.fim.ai/configuration/channels/overview) for details.

## 設定

FIM One は**任意の OpenAI 互換プロバイダー**で動作します:

| プロバイダー       | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | -------------------- |
| **OpenAI**         | `sk-...`      | *(デフォルト)*                 | `gpt-4o`             |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`      |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6`  |
| **Ollama** (ローカル) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`        |

最小限の `.env`:

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default
# LLM_MODEL=gpt-4o                         # default
JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

> 完全なリファレンス: [環境変数](https://docs.fim.ai/configuration/environment-variables)

## Tech Stack

| Layer       | Technology                                                          |
| ----------- | ------------------------------------------------------------------- |
| Backend     | Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio                 |
| Frontend    | Next.js 15, React 18, Tailwind CSS, shadcn/ui, React Flow v12      |
| AI / RAG    | OpenAI-compatible LLMs, Jina AI (embed + search), LanceDB          |
| Database    | SQLite (dev) / PostgreSQL (prod)                                    |
| Messaging   | `BaseChannel` abstraction (Feishu/Lark shipped), Fernet-encrypted credentials, HMAC signature verification |
| Infra       | Docker, uv, pnpm, SSE streaming                                    |

## 開発

```bash
uv sync --all-extras          # install dependencies
pytest                         # run tests
pytest --cov=fim_one           # with coverage
ruff check src/ tests/         # lint
mypy src/                      # type check
bash scripts/setup-hooks.sh    # install git hooks (enables auto i18n)
```

## ロードマップ

バージョン履歴と計画中の機能については、完全な[ロードマップ](https://docs.fim.ai/roadmap)を参照してください。

## FAQ

デプロイメント、LLMプロバイダー、システム要件など、よくある質問については、[FAQ](https://docs.fim.ai/faq)を参照してください。

## Contributing

We welcome contributions of all kinds — code, docs, translations, bug reports, and ideas.

> **Pioneer Program**: The first 100 contributors who get a PR merged are recognized as **Founding Contributors** with permanent credits, a badge, and priority issue support. [Learn more &rarr;](CONTRIBUTING.md#-pioneer-program)

**Quick links:**

- [**Contributing Guide**](CONTRIBUTING.md) — setup, conventions, PR process
- [**Development Conventions**](https://docs.fim.ai/contributing) — type safety, testing, and code quality standards
- [**Good First Issues**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — curated for newcomers
- [**Open Issues**](https://github.com/fim-ai/fim-one/issues) — bugs & feature requests

**Security:** Do not open a public issue for a sensitive vulnerability. Report it privately through [GitHub Security Advisories](https://github.com/fim-ai/fim-one/security/advisories/new) or email security@fim.ai. See [SECURITY.md](SECURITY.md).

## アクティビティ

![Alt](https://repobeats.axiom.co/api/embed/49402c7d85e343e9cb5909da7b48db1930c76554.svg "Repobeats analytics image")

## Contributors

Thanks to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/tao-hpu"><img src="https://avatars.githubusercontent.com/u/1250043?v=4?s=80" width="80px;" alt="Tao An"/><br /><sub><b>Tao An</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Code">💻</a> <a href="#maintenance-tao-hpu" title="Maintenance">🚧</a> <a href="#design-tao-hpu" title="Design">🎨</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Documentation">📖</a> <a href="#projectManagement-tao-hpu" title="Project Management">📆</a> <a href="#ideas-tao-hpu" title="Ideas, Planning, & Feedback">🤔</a> <a href="#infra-tao-hpu" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="https://github.com/fim-ai/fim-one/issues?q=author%3Atao-hpu" title="Bug reports">🐛</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/tgonzalezc5"><img src="https://avatars.githubusercontent.com/u/102870299?v=4?s=80" width="80px;" alt="Teo Gonzalez Collazo"/><br /><sub><b>Teo Gonzalez Collazo</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Code">💻</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/Houjiawei330"><img src="https://avatars.githubusercontent.com/u/49180524?v=4?s=80" width="80px;" alt="Houx."/><br /><sub><b>Houx.</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=Houjiawei330" title="Code">💻</a> <a href="https://github.com/fim-ai/fim-one/issues?q=author%3AHoujiawei330" title="Bug reports">🐛</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/wcboy"><img src="https://avatars.githubusercontent.com/u/85050716?v=4?s=80" width="80px;" alt="Chenying Wang"/><br /><sub><b>Chenying Wang</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=wcboy" title="Code">💻</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/Joshua-Medvinsky"><img src="https://avatars.githubusercontent.com/u/76570188?v=4?s=80" width="80px;" alt="Joshua Medvinsky"/><br /><sub><b>Joshua Medvinsky</b></sub></a><br /><a href="#security-Joshua-Medvinsky" title="Security">🛡️</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/Aryan-theccool"><img src="https://avatars.githubusercontent.com/u/146411259?v=4?s=80" width="80px;" alt="Aryan singh Bhadoria"/><br /><sub><b>Aryan singh Bhadoria</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/issues?q=author%3AAryan-theccool" title="Bug reports">🐛</a> <a href="https://github.com/fim-ai/fim-one/commits?author=Aryan-theccool" title="Documentation">📖</a> <a href="https://github.com/fim-ai/fim-one/commits?author=Aryan-theccool" title="Tests">⚠️</a> <a href="https://github.com/fim-ai/fim-one/commits?author=Aryan-theccool" title="Code">💻</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://portfolio-alexgonzalez33.vercel.app/"><img src="https://avatars.githubusercontent.com/u/82875882?v=4?s=80" width="80px;" alt="alexcastgonz"/><br /><sub><b>alexcastgonz</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/issues?q=author%3Apcbeingused333" title="Bug reports">🐛</a> <a href="https://github.com/fim-ai/fim-one/commits?author=pcbeingused333" title="Documentation">📖</a> <a href="https://github.com/fim-ai/fim-one/commits?author=pcbeingused333" title="Tests">⚠️</a> <a href="https://github.com/fim-ai/fim-one/commits?author=pcbeingused333" title="Code">💻</a></td>
      <td align="center" valign="top" width="12.5%"><a href="https://github.com/ryangoho"><img src="https://avatars.githubusercontent.com/u/320067612?v=4?s=80" width="80px;" alt="Ryan Goh"/><br /><sub><b>Ryan Goh</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/issues?q=author%3Aryangoho" title="Bug reports">🐛</a> <a href="https://github.com/fim-ai/fim-one/commits?author=ryangoho" title="Documentation">📖</a> <a href="https://github.com/fim-ai/fim-one/commits?author=ryangoho" title="Tests">⚠️</a> <a href="https://github.com/fim-ai/fim-one/commits?author=ryangoho" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://allcontributors.org/) specification. Contributions of any kind welcome!

## FIMについて

FIM Oneは[FIM](https://fim.ai)によって開発されています。FIMは、FIM Labs Pte. Ltd.（シンガポール）とBeijing FIM Network Technology Co., Ltd.（北京、2013年設立）という2つの独立した企業が共有するブランドです。

## ライセンス

FIM One Source Available License。これは**OSI認定のオープンソースライセンスではありません**。

**許可される用途**: 内部使用、修正、ライセンスを保持した配布、競合しないアプリケーションへの組み込み。

**制限される用途**: マルチテナント SaaS、競合するエージェントプラットフォーム、ホワイトラベル、ブランディングの削除。

商用ライセンスのお問い合わせは、[GitHub](https://github.com/fim-ai/fim-one) でイシューを開いてください。

詳細は [LICENSE](LICENSE) をご覧ください。

---

<div align="center">

🌐 [ウェブサイト](https://one.fim.ai/) · 📖 [ドキュメント](https://docs.fim.ai) · 📋 [変更履歴](https://docs.fim.ai/changelog) · 🐛 [バグ報告](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

<div align="center">

![FIM One 배너](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![CI](https://github.com/fim-ai/fim-one/actions/workflows/test.yml/badge.svg)](https://github.com/fim-ai/fim-one/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**글로벌 × 중국 기업을 위한 올인원 에이전트 플랫폼.**
*하나의 에이전트 코어를 통해 이미 사용 중인 모든 시스템을 연결하세요 — 글로벌 SaaS부터 중국 스택까지.*

🌐 [웹사이트](https://one.fim.ai/) · 📖 [문서](https://docs.fim.ai) · 📋 [변경 로그](https://docs.fim.ai/changelog) · 🐛 [버그 신고](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ 설정을 건너뛰고 Cloud에서 FIM One을 사용해 보세요.**
> [cloud.fim.ai](https://cloud.fim.ai/)에서 관리형 버전을 이용할 수 있습니다 — Docker, API 키, 구성 설정이 필요하지 않습니다. 로그인하고 몇 초 만에 시스템 연결을 시작하세요. _얼리 액세스 단계이며, 여러분의 피드백을 환영합니다._

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

### 스크린샷

**대시보드** — 통계, 활동 트렌드, 토큰 사용량, 에이전트 및 대화에 대한 빠른 액세스.

![Dashboard](./assets/screenshot-dashboard.png)

**에이전트 채팅** — 연결된 데이터베이스에 대한 다단계 도구 호출을 포함한 ReAct 추론.

![Agent Chat](./assets/screenshot-agent-chat.png)

**DAG 플래너** — LLM이 생성한 실행 계획(병렬 단계 및 실시간 상태 추적 포함).

![DAG Planner](./assets/screenshot-dag-planner.png)

### 데모

**에이전트 사용**

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

**플래너 모드 사용**

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## 빠른 시작

### Docker (권장)

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

docker compose up --build -d
```

http://localhost:3000 을 열어주세요 — 처음 실행할 때 관리자 계정을 만들게 됩니다. 끝입니다.

```bash
docker compose up -d          # start
docker compose down           # stop
docker compose logs -f        # view logs
```

### 로컬 개발

필수 요구사항: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, pnpm.

```bash
git clone https://github.com/fim-ai/fim-one.git && cd fim-one

cp example.env .env           # Edit: set LLM_API_KEY

uv sync --all-extras
cd frontend && pnpm install && cd ..

./start.sh dev                # hot reload: Python --reload + Next.js HMR
```

| 명령어          | 시작되는 항목                       | URL                            |
| ---------------- | --------------------------------- | ------------------------------ |
| `./start.sh`         | Next.js + FastAPI                 | localhost:3000 (UI) + :8000    |
| `./start.sh dev`     | 동일, 핫 리로드 포함             | 동일                           |
| `./start.sh dev:api` | API만, 개발 모드 (핫 리로드)   | localhost:8000                 |
| `./start.sh dev:ui`  | 프론트엔드만, 개발 모드 (HMR)    | localhost:3000                 |
| `./start.sh api`     | FastAPI만 (헤드리스)           | localhost:8000/api             |

> 프로덕션 배포(Docker, 리버스 프록시, 무중단 업데이트)의 경우 [배포 가이드](https://docs.fim.ai/quickstart#production-deployment)를 참조하세요.

## 주요 기능

#### 국경 간 연결성
- **세 가지 제공 모드** — 독립형 어시스턴트, 임베디드 Copilot 또는 중앙 Hub를 사용할 수 있으며, 모두 동일한 에이전트 코어를 사용합니다.
- **모든 시스템에 하나의 패턴** — API, 데이터베이스, MCP 서버를 연결합니다. 작업은 인증 주입과 함께 에이전트 도구로 자동 등록됩니다. 점진적 공개 메타 도구를 통해 모든 도구 유형에서 token 사용량을 80% 이상 줄일 수 있습니다.
- **데이터베이스 커넥터** — 대부분의 글로벌 플랫폼이 연결할 수 없는 PostgreSQL, MySQL 및 중국에서 널리 사용되는 엔터프라이즈 데이터베이스(DM, KingbaseES, HighGo)를 지원합니다. 스키마 인트로스펙션 및 AI 기반 주석 처리를 제공합니다.
- **세 가지 구축 방법** — OpenAPI 사양 가져오기, AI 채팅 빌더 사용 또는 MCP 서버 직접 연결을 지원합니다.

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

## 설정

FIM One은 **모든 OpenAI 호환 제공자**와 함께 작동합니다:

| 제공자           | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | -------------------- |
| **OpenAI**         | `sk-...`      | *(기본값)*                    | `gpt-4o`             |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`      |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6`  |
| **Ollama** (로컬) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`        |

최소 `.env`:

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default
# LLM_MODEL=gpt-4o                         # default
JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

> 전체 참고: [환경 변수](https://docs.fim.ai/configuration/environment-variables)

## Tech Stack

| Layer       | Technology                                                          |
| ----------- | ------------------------------------------------------------------- |
| Backend     | Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio                 |
| Frontend    | Next.js 15, React 18, Tailwind CSS, shadcn/ui, React Flow v12      |
| AI / RAG    | OpenAI-compatible LLMs, Jina AI (embed + search), LanceDB          |
| Database    | SQLite (dev) / PostgreSQL (prod)                                    |
| Messaging   | `BaseChannel` abstraction (Feishu/Lark shipped), Fernet-encrypted credentials, HMAC signature verification |
| Infra       | Docker, uv, pnpm, SSE streaming                                    |

## 개발

```bash
uv sync --all-extras          # install dependencies
pytest                         # run tests
pytest --cov=fim_one           # with coverage
ruff check src/ tests/         # lint
mypy src/                      # type check
bash scripts/setup-hooks.sh    # install git hooks (enables auto i18n)
```

## 로드맵

전체 [로드맵](https://docs.fim.ai/roadmap)에서 버전 히스토리 및 계획된 기능을 확인하세요.

## FAQ

배포, LLM 제공자, 시스템 요구사항 등에 대한 일반적인 질문 — [FAQ](https://docs.fim.ai/faq)를 참조하세요.

## Contributing

We welcome contributions of all kinds — code, docs, translations, bug reports, and ideas.

> **Pioneer Program**: The first 100 contributors who get a PR merged are recognized as **Founding Contributors** with permanent credits, a badge, and priority issue support. [Learn more &rarr;](CONTRIBUTING.md#-pioneer-program)

**Quick links:**

- [**Contributing Guide**](CONTRIBUTING.md) — setup, conventions, PR process
- [**Development Conventions**](https://docs.fim.ai/contributing) — type safety, testing, and code quality standards
- [**Good First Issues**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — curated for newcomers
- [**Open Issues**](https://github.com/fim-ai/fim-one/issues) — bugs & feature requests

**Security:** Do not open a public issue for a sensitive vulnerability. Report it privately through [GitHub Security Advisories](https://github.com/fim-ai/fim-one/security/advisories/new) or email security@fim.ai. See [SECURITY.md](SECURITY.md).

## 활동

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

## FIM 소개

FIM One은 [FIM](https://fim.ai)에서 개발합니다. FIM은 서로 독립적인 두 회사가 공동으로 사용하는 브랜드입니다. FIM Labs Pte. Ltd. (싱가포르) 및 Beijing FIM Network Technology Co., Ltd. (베이징, 2013년 설립).

## 라이선스

FIM One Source Available License. 이는 **OSI 승인 오픈 소스 라이선스가 아닙니다**.

**허용**: 내부 사용, 수정, 라이선스 유지 배포, 경쟁하지 않는 애플리케이션에 임베딩.

**제한**: 멀티테넌트 SaaS, 경쟁하는 에이전트 플랫폼, 화이트 라벨링, 브랜딩 제거.

상용 라이선싱 문의는 [GitHub](https://github.com/fim-ai/fim-one)에서 이슈를 열어주세요.

전체 약관은 [LICENSE](LICENSE)를 참조하세요.

---

<div align="center">

🌐 [웹사이트](https://one.fim.ai/) · 📖 [문서](https://docs.fim.ai) · 📋 [변경 로그](https://docs.fim.ai/changelog) · 🐛 [버그 보고](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

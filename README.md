<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![GitHub stars](https://img.shields.io/github/stars/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/network)
[![GitHub issues](https://img.shields.io/github/issues/fim-ai/fim-one)](https://github.com/fim-ai/fim-one/issues)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Product Hunt](https://img.shields.io/badge/Product%20Hunt-FIM%20One-DA552F?logo=producthunt&logoColor=white)](https://www.producthunt.com/products/fim-one)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**Your systems don't talk to each other. FIM One connects them all to AI — zero code changes, zero data migration.**

*AI-Powered Connector Hub — embed into one system as a Copilot, or connect them all as a Hub.*

🌐 [Website](https://one.fim.ai/) · 📖 [Docs](https://docs.fim.ai) · 📋 [Changelog](https://docs.fim.ai/changelog) · 🐛 [Report Bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ Skip the setup — try FIM One on Cloud.**
> A managed version is live at **[cloud.fim.ai](https://cloud.fim.ai/)**: no Docker, no API keys, no config. Sign in and start connecting your systems in seconds. _Early access, feedback welcome._

---

## Table of Contents

- [Overview](#overview)
- [Use Cases](#use-cases)
- [Why FIM One](#why-fim-one)
- [Where FIM One Sits](#where-fim-one-sits)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Quick Start](#quick-start) (Docker / Local / Production)
- [Configuration](#configuration)
- [Development](#development)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [Star History](#star-history)
- [Activity](#activity)
- [Contributors](#contributors)
- [License](#license)

## Overview

Every company has systems that don't talk to each other — ERP, CRM, OA, finance, HR, custom databases. Each vendor's AI is smart inside its own walls, but blind to everything else. FIM One is the **external, third-party hub** that connects them all through AI — without modifying your existing infrastructure. Three delivery modes, one agent core:

| Mode           | What it is                                                                       | How you access it                       |
| -------------- | -------------------------------------------------------------------------------- | --------------------------------------- |
| **Standalone** | General-purpose AI assistant — search, code, knowledge base                      | Portal                                  |
| **Copilot**    | AI embedded in a host system — works alongside users in their existing UI        | iframe / widget / embed into host pages |
| **Hub**        | Central AI orchestration — all your systems connected, cross-system intelligence | Portal / API                            |

```mermaid
graph LR
    ERP --> Hub["FIM One Hub<br/>(AI orchestration)"]
    Database --> Hub
    Lark --> Hub
    CRM --> Hub
    OA --> Hub
    API[Custom API] --> Hub
```

The core is always the same: ReAct reasoning loops, dynamic DAG planning with concurrent execution, pluggable tools, and a protocol-first architecture with zero vendor lock-in.

### Using Agents

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

### Using Planner Mode

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## Use Cases

Enterprise data and workflows are locked inside OA, ERP, finance, and approval systems. FIM One lets AI agents read and write those systems — automating cross-system processes without modifying your existing infrastructure.

| Scenario                  | Recommended Start | What it automates                                                                                                |
| ------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| **Legal & Compliance**    | Copilot → Hub     | Contract clause extraction, version diff, risk flagging with source citations, auto-trigger OA approval          |
| **IT Operations**         | Hub               | Alert fires → logs pulled → root cause analyzed → fix dispatched to Lark/Slack — one closed loop                 |
| **Business Operations**   | Copilot           | Scheduled data summaries pushed to team channels; ad-hoc natural language queries against live databases         |
| **Finance Automation**    | Hub               | Invoice verification, expense approval routing, ledger reconciliation across ERP and accounting systems          |
| **Procurement**           | Copilot → Hub     | Requirements → vendor comparison → contract draft → approval — Agent handles the cross-system handoffs           |
| **Developer Integration** | API               | Import an OpenAPI spec or describe an API in chat — connector created in minutes, auto-registered as agent tools |

## Why FIM One

### Land and Expand

Start by embedding a **Copilot** into one system — say, your ERP. Users interact with AI right inside their familiar interface: query financial data, generate reports, get answers without leaving the page.

When the value is proven, set up a **Hub** — a central portal that connects all your systems together. The ERP Copilot keeps running embedded; the Hub adds cross-system orchestration: query contracts in CRM, check approvals in OA, notify stakeholders on Lark — all from one place.

Copilot proves value inside one system. Hub unlocks value across all systems.

### What FIM One Does NOT Do

FIM One does not replicate workflow logic that already exists in your target systems:

- **No BPM/FSM engine** — Approval chains, routing, escalation, and state machines are the target system's responsibility. These systems spent years building this logic.
- **No BPM/FSM workflow engine** — FIM One's Workflow Blueprints are automation templates (LLM calls, condition branches, connector actions), not business process management. Approval chains, routing rules, and state machines belong in the target system.
- **Connector = API call** — From the connector's perspective, "transfer approval" = one API call, "reject with reason" = one API call. All complex workflow operations collapse to HTTP requests. FIM One calls the API; the target system manages the state.

This is a deliberate architectural boundary, not a capability gap.

### Competitive Positioning

|                        | Dify                       | Manus            | Coze                  | FIM One                      |
| ---------------------- | -------------------------- | ---------------- | --------------------- | ---------------------------- |
| **Approach**           | Visual workflow builder    | Autonomous agent | Builder + agent space | AI Connector Hub             |
| **Planning**           | Human-designed static DAGs | Multi-agent CoT  | Static + dynamic      | LLM DAG planning + ReAct     |
| **Cross-system**       | API nodes (manual)         | No               | Plugin marketplace    | Hub Mode (N:N orchestration) |
| **Human Confirmation** | No                         | No               | No                    | Yes (pre-execution gate)     |
| **Self-hosted**        | Yes (Docker stack)         | No               | Yes (Coze Studio)     | Yes (single process)         |

> Deep dive: [Philosophy](https://docs.fim.ai/architecture/philosophy) | [Execution Modes](https://docs.fim.ai/concepts/execution-modes) | [Competitive Landscape](https://docs.fim.ai/strategy/competitive-landscape)

### Where FIM One Sits

```
                Static Execution          Dynamic Execution
            ┌──────────────────────┬──────────────────────┐
 Static     │ BPM / Workflow       │ ACM                  │
 Planning   │ Camunda, Activiti    │ (Salesforce Case)    │
            │ Dify, n8n, Coze     │                      │
            ├──────────────────────┼──────────────────────┤
 Dynamic    │ (transitional —      │ Autonomous Agent     │
 Planning   │  unstable quadrant)  │ AutoGPT, Manus       │
            │                      │ ★ FIM One (bounded)│
            └──────────────────────┴──────────────────────┘
```

Dify/n8n are **Static Planning + Static Execution** — humans design the DAG on a visual canvas, nodes execute fixed operations. FIM One is **Dynamic Planning + Dynamic Execution** — LLM generates the DAG at runtime, each node runs a ReAct loop, with re-planning when goals aren't met. But bounded (max 3 re-plan rounds, token budgets, confirmation gates), so more controlled than AutoGPT.

FIM One doesn't do BPM/FSM — workflow logic belongs to the target system, Connectors just call APIs.

> Full explanation: [Philosophy](https://docs.fim.ai/architecture/philosophy)

## Key Features

#### Connector Platform (the core)
- **Connector Hub Architecture** — Standalone assistant, embedded Copilot, or central Hub — same agent core, different delivery.
- **Any System, One Pattern** — Connect APIs, databases, and message buses. Actions auto-register as agent tools with auth injection (Bearer, API Key, Basic).
- **Database Connectors** — Direct SQL access to PostgreSQL, MySQL, Oracle, SQL Server, and Chinese legacy databases (DM, KingbaseES, GBase, Highgo). Schema introspection, AI-powered annotation, read-only query execution, and encrypted credentials at rest. Each DB connector auto-generates 3 tools (`list_tables`, `describe_table`, `query`).
- **Three Ways to Build Connectors:**
  - *Import OpenAPI spec* — upload YAML/JSON/URL; connectors and all actions generated automatically.
  - *AI chat builder* — describe the API in natural language; AI generates and iterates the action config in-conversation. 10 specialized builder tools handle connector settings, actions, testing, and agent wiring.
  - *MCP ecosystem* — connect any MCP server directly; the third-party MCP community works out of the box.

#### Intelligent Planning & Execution
- **Dynamic DAG Planning** — LLM decomposes goals into dependency graphs at runtime. No hard-coded workflows.
- **Concurrent Execution** — Independent steps run in parallel via asyncio.
- **DAG Re-Planning** — Auto-revises the plan up to 3 rounds when goals aren't met.
- **ReAct Agent** — Structured reasoning-and-acting loop with automatic error recovery.
- **Auto-Routing** — Automatic query classification routes each request to the optimal execution mode (ReAct or DAG). Frontend supports 3-way toggle (Auto/Standard/Planner). Configurable via `AUTO_ROUTING`.
- **Extended Thinking** — Enable chain-of-thought reasoning for supported models (OpenAI o-series, Gemini 2.5+, Claude) via `LLM_REASONING_EFFORT`. The model's reasoning is surfaced in the UI "thinking" step.

#### Workflow Blueprints
- **Visual Workflow Editor** — Design multi-step automation blueprints with a drag-and-drop canvas built on React Flow v12. 12 node types: Start, End, LLM, Condition Branch, Question Classifier, Agent, Knowledge Retrieval, Connector, HTTP Request, Variable Assign, Template Transform, Code Execution.
- **Topological Execution Engine** — Workflows execute nodes in dependency order with condition branching, cross-node variable passing, and real-time SSE status streaming.
- **Import/Export** — Share workflow blueprints as JSON. Encrypted environment variables for secure credential handling.

#### Tools & Integrations
- **Pluggable Tool System** — Auto-discovery; ships with Python executor, Node.js executor, calculator, web search/fetch, HTTP request, shell exec, and more.
- **Pluggable Sandbox** — `python_exec` / `node_exec` / `shell_exec` run in local or Docker mode (`CODE_EXEC_BACKEND=docker`) for OS-level isolation (`--network=none`, `--memory=256m`). Safe for SaaS and multi-tenant deployments.
- **MCP Protocol** — Connect any MCP server as tools. Third-party MCP ecosystem works out of the box.
- **Tool Artifact System** — Tools produce rich outputs (HTML previews, generated files) with in-chat rendering and download. HTML artifacts render in sandboxed iframes; file artifacts show download chips.
- **OpenAI-Compatible** — Works with any `/v1/chat/completions` provider (OpenAI, DeepSeek, Qwen, Ollama, vLLM…).

#### RAG & Knowledge
- **Full RAG Pipeline** — Jina embedding + LanceDB + FTS + RRF hybrid retrieval + reranker. Supports PDF, DOCX, Markdown, HTML, CSV.
- **Grounded Generation** — Evidence-anchored RAG with inline `[N]` citations, conflict detection, and explainable confidence scores.
- **KB Document Management** — Chunk-level CRUD, text search across chunks, failed document retry, and auto-migrating vector store schema.

#### Portal & UX
- **Real-time Streaming (SSE v2)** — Split event protocol (`done` / `suggestions` / `title` / `end`) with streaming dot-pulse cursor, KaTeX math rendering, and tool step folding.
- **DAG Visualization** — Interactive flow graph with live status, dependency edges, click-to-scroll, and re-plan round snapshots as collapsible cards.
- **Conversational Interrupt** — Send follow-up messages while the agent is running; injected at the next iteration boundary.
- **Dark / Light / System Theme** — Full theme support with system-preference detection.
- **Command Palette** — Conversation search, starring, batch operations, and title rename.

#### Platform & Multi-Tenant
- **JWT Auth** — Token-based SSE auth, conversation ownership, per-user resource isolation.
- **Agent Management** — Create, configure, and publish agents with bound models, tools, and instructions. Per-agent execution mode (Standard/Planner) and temperature control. Optional `discoverable` flag enables LLM auto-discovery via CallAgentTool.
- **Agent Skill System** — Progressive instruction loading for agents. Skills (SOPs, scripts, domain knowledge) are referenced in the system prompt as lightweight stubs (~15 tokens each); agents call `read_skill(name)` to load full content on demand, reducing per-conversation instruction token cost by ~80%. Per-agent `compact_instructions` field for custom ContextGuard compaction strategy.
- **Marketplace (Shadow Market Org)** — Built-in Market org operates as an invisible backend entity for resource sharing. Resources are discovered via marketplace browsing and explicitly subscribed to (pull model) — no auto-join membership. Publishing to the marketplace always requires review.
- **Resource Subscriptions** — Users browse and subscribe to shared resources from the Marketplace. Subscribe/unsubscribe via UI or API. All resource types (agents, connectors, knowledge bases, MCP servers, skills, workflows) support marketplace publishing and subscription management.
- **Admin Panel** — System stats dashboard (users, conversations, tokens, model usage charts, tokens-by-agent breakdown), connector call metrics (success rate, latency, call counts), user management with search/pagination, role toggle, password reset, account enable/disable, and per-tool enable/disable controls.
- **First-Run Setup Wizard** — On first launch, the portal guides you through creating an admin account (username, password, email). This one-time setup becomes your login credential — no config files needed.
- **Personal Center** — Per-user global system instructions, applied across all conversations.
- **Language Preference** — Per-user language setting (auto/en/zh) that directs all LLM responses to the chosen language.

#### Context & Memory
- **LLM Compact** — Automatic LLM-powered summarization to stay within token budgets.
- **ContextGuard + Pinned Messages** — Token budget manager; pinned messages are protected from compaction.
- **Dual Database Support** — SQLite (zero-config default) for getting started in seconds; PostgreSQL for production and multi-worker deployments. Docker Compose auto-provisions PostgreSQL with health checks. `docker compose up` and you're live.

## Architecture

### System Overview

```mermaid
graph TB
    subgraph app["Application & Interaction Layer"]
        a["Portal · API · iframe · Lark/Slack Bot · Webhook · WeCom/DingTalk"]
    end
    subgraph mid["FIM One Middleware"]
        direction LR
        m1["Connectors<br/>+ MCP Hub"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems & Data Layer"]
        b["ERP · CRM · OA · Finance · Databases · Custom APIs<br/>Lark · DingTalk · WeCom · Slack · Email · Webhook"]
    end
    app --> mid --> biz
```

### Connector Hub

```mermaid
graph LR
    ERP["ERP<br/>(SAP/Kingdee)"] --> A
    CRM["CRM<br/>(Salesforce)"] --> B
    OA["OA<br/>(Seeyon/Weaver)"] --> C
    DB["Custom DB<br/>(PG/MySQL)"] --> D
    subgraph Hub["FIM One Hub"]
        A["Agent A: Finance Audit"]
        B["Agent B: Contract Review"]
        C["Agent C: Approval Assist"]
        D["Agent D: Data Reporting"]
    end
    A --> O1["Lark / Slack"]
    B --> O2["Email / WeCom"]
    C --> O3["Teams / Webhook"]
    D --> O4["Any API"]
```

*Portal / API / iframe*

Each connector is a standardized bridge — the agent doesn't know or care whether it's talking to SAP or a custom PostgreSQL database. See [Connector Architecture](https://docs.fim.ai/architecture/connector-architecture) for details.

### Internal Execution

FIM One provides two execution modes, with automatic routing between them:

| Mode         | Best for                  | How it works                                                       |
| ------------ | ------------------------- | ------------------------------------------------------------------ |
| Auto         | All queries (default)     | Fast LLM classifies the query and routes to ReAct or DAG           |
| ReAct        | Single complex queries    | Reason → Act → Observe loop with tools                             |
| DAG Planning | Multi-step parallel tasks | LLM generates dependency graph, independent steps run concurrently |

```mermaid
graph TB
    Q[User Query] --> P["DAG Planner<br/>LLM decomposes the goal into steps + dependency edges"]
    P --> E["DAG Executor<br/>Launches independent steps concurrently via asyncio<br/>Each step is handled by a ReAct Agent"]
    E --> R1["ReAct Agent 1 → Tools<br/>(python_exec, custom, ...)"]
    E --> R2["ReAct Agent 2 → RAG<br/>(retriever interface)"]
    E --> RN["ReAct Agent N → ..."]
    R1 & R2 & RN --> An["Plan Analyzer<br/>LLM evaluates results · re-plans if goal not met"]
    An --> F[Final Answer]
```

## Quick Start

### Option A: Docker (recommended)

No local Python or Node.js required — everything is built inside the container.

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

# Configure — only LLM_API_KEY is required
cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

# Build and run (first time, or after pulling new code)
docker compose up --build -d
```

Open http://localhost:3000 — on first launch you'll be guided through creating an admin account. That's it.

After the initial build, subsequent starts only need:

```bash
docker compose up -d          # start (skip rebuild if image unchanged)
docker compose down           # stop
docker compose logs -f        # view logs
```

Data is persisted in Docker named volumes (`fim-data`, `fim-uploads`) and survives container restarts.

> **Note:** Docker mode does not support hot reload. Code changes require rebuilding the image (`docker compose up --build -d`). For active development with live reload, use **Option B** below.

### Option B: Local Development

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, pnpm.

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY

# Install
uv sync --all-extras
cd frontend && pnpm install && cd ..

# Launch (with hot reload)
./start.sh dev
```

| Command          | What starts                                             | URL                                      |
| ---------------- | ------------------------------------------------------- | ---------------------------------------- |
| `./start.sh`     | Next.js + FastAPI                                       | http://localhost:3000 (UI) + :8000 (API) |
| `./start.sh dev` | Same, with hot reload (Python `--reload` + Next.js HMR) | Same                                     |
| `./start.sh api` | FastAPI only (headless, for integration or testing)     | http://localhost:8000/api                |

### Production Deployment

Both options work in production:

| Method     | Command                | Best for                                    |
| ---------- | ---------------------- | ------------------------------------------- |
| **Docker** | `docker compose up -d` | Hands-off deployment, easy updates          |
| **Script** | `./start.sh`           | Bare-metal servers, custom process managers |

For either method, put an Nginx reverse proxy in front for HTTPS and custom domain:

```
User → Nginx (443/HTTPS) → localhost:3000
```

The API runs internally on port 8000 — Next.js proxies `/api/*` requests automatically. Only port 3000 needs to be exposed.

**Updating a running deployment** (zero-downtime):

```bash
cd /path/to/fim-one \
  && git pull origin master \
  && sudo docker compose build \
  && sudo docker compose up -d \
  && sudo docker image prune -f
```

`build` runs first while old containers keep serving traffic. `up -d` then replaces only the containers whose image changed — downtime is ~10 seconds instead of minutes.

If you use the code execution sandbox (`CODE_EXEC_BACKEND=docker`), mount the Docker socket:

```yaml
# docker-compose.yml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

## Configuration

### Recommended Setup

FIM One works with **any OpenAI-compatible LLM provider** — OpenAI, DeepSeek, Anthropic, Qwen, Ollama, vLLM, and more. Pick whichever you prefer:

| Provider           | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | ------------------- |
| **OpenAI**         | `sk-...`      | *(default)*                    | `gpt-4o`            |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`     |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6` |
| **Ollama** (local) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`       |

**[Jina AI](https://jina.ai/)** unlocks web search/fetch, embedding, and the full RAG pipeline (free tier available).

Minimal `.env`:

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default — change for other providers
# LLM_MODEL=gpt-4o                         # default — change for other models

JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

### All Variables

See the full [Environment Variables](https://docs.fim.ai/configuration/environment-variables) reference for all configuration options (LLM, agent execution, web tools, RAG, code execution, image generation, connectors, platform, OAuth).

## Development

```bash
# Install all dependencies (including dev extras)
uv sync --all-extras

# Run tests
pytest

# Run tests with coverage
pytest --cov=fim_one --cov-report=term-missing

# Lint
ruff check src/ tests/

# Type check
mypy src/

# Install git hooks (run once after clone — enables auto i18n translation on commit)
bash scripts/setup-hooks.sh
```

## Internationalization (i18n)

FIM One supports **6 languages**: English, Chinese, Japanese, Korean, German, and French. Translations are fully automated — you only need to edit English source files.

**Supported languages**: `en` `zh` `ja` `ko` `de` `fr`

| What | Source (edit this) | Auto-generated (don't edit) |
|------|--------------------|-----------------------------|
| UI strings | `frontend/messages/en/*.json` | `frontend/messages/{locale}/*.json` |
| Documentation | `docs/*.mdx` | `docs/{locale}/*.mdx` |
| README | `README.md` | `README.{locale}.md` |

**How it works**: A pre-commit hook detects changes to English files and translates them via the project's Fast LLM. Translations are incremental — only new, modified, or deleted content is processed.

```bash
# Setup (run once after clone)
bash scripts/setup-hooks.sh

# Full translation (first-time or after adding a new locale)
uv run scripts/translate.py --all

# Translate specific files
uv run scripts/translate.py --files frontend/messages/en/common.json

# Override target locales
uv run scripts/translate.py --all --locale ja ko

# Increase parallel API calls (default: 3, raise if your API allows)
uv run scripts/translate.py --all --concurrency 10

# Daily workflow: just commit — the hook handles everything automatically
git add frontend/messages/en/common.json
git commit -m "feat(i18n): add new strings"  # hook auto-translates
```

| Flag | Default | Description |
|------|---------|-------------|
| `--all` | — | Retranslate everything (ignore cache) |
| `--files` | — | Translate specific files only |
| `--locale` | auto-discover | Override target locales |
| `--concurrency` | 3 | Max parallel LLM API calls |
| `--force` | — | Force retranslation of all JSON keys |

**Adding a new language**: `mkdir frontend/messages/{locale}` → run `--all` → add locale to `frontend/src/i18n/request.ts` `SUPPORTED_LOCALES`.

## Roadmap

See the full [Roadmap](https://docs.fim.ai/roadmap) for version history and what's next.

## Contributing

We welcome contributions of all kinds — code, docs, translations, bug reports, and ideas.

> **Pioneer Program**: The first 100 contributors who get a PR merged are recognized as **Founding Contributors** with permanent credits in the project, a badge on their profile, and priority issue support. [Learn more &rarr;](CONTRIBUTING.md#-pioneer-program)

**Quick links:**

- [**Contributing Guide**](CONTRIBUTING.md) — setup, conventions, PR process
- [**Good First Issues**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — curated for newcomers
- [**Open Issues**](https://github.com/fim-ai/fim-one/issues) — bugs & feature requests

## Star History

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>

## Activity

![Alt](https://repobeats.axiom.co/api/embed/ff8b449fd183a323d81f33fc96e7ce42ad745f65.svg "Repobeats analytics image")

## Contributors

Thanks to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

[![Contributors](https://contrib.rocks/image?repo=fim-ai/fim-one)](https://github.com/fim-ai/fim-one/graphs/contributors)

This project follows the [all-contributors](https://allcontributors.org/) specification. Contributions of any kind welcome!

## License

FIM One Source Available License. This is **not** an OSI-approved open source license.

**Permitted**: internal use, modification, distribution with license intact, embedding in your own (non-competing) applications.

**Restricted**: multi-tenant SaaS, competing agent platforms, white-labeling, removing branding.

For commercial licensing inquiries, please open an issue on [GitHub](https://github.com/fim-ai/fim-one).

See [LICENSE](LICENSE) for full terms.

---

<div align="center">

🌐 [Website](https://one.fim.ai/) · 📖 [Docs](https://docs.fim.ai) · 📋 [Changelog](https://docs.fim.ai/changelog) · 🐛 [Report Bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

<div align="center">

![FIM Agent Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![GitHub stars](https://img.shields.io/github/stars/fim-ai/fim-agent?style=social)](https://github.com/fim-ai/fim-agent/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/fim-ai/fim-agent?style=social)](https://github.com/fim-ai/fim-agent/network)
[![GitHub issues](https://img.shields.io/github/issues/fim-ai/fim-agent)](https://github.com/fim-ai/fim-agent/issues)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_TECH?style=social)](https://x.com/FIM_TECH)

**AI-Powered Connector Hub — embed into one system as a Copilot, or connect them all as a Hub.**

🌐 [Website](https://agent.fim.ai/) · 📖 [Wiki](https://github.com/fim-ai/fim-agent/wiki) · 📋 [Changelog](https://github.com/fim-ai/fim-agent/wiki/CHANGELOG) · 🐛 [Report Bug](https://github.com/fim-ai/fim-agent/issues) · 🐦 [Twitter](https://x.com/FIM_TECH)

</div>

---

## Table of Contents

- [Overview](#overview)
- [Why FIM Agent](#why-fim-agent)
- [Where FIM Agent Sits](#where-fim-agent-sits)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Development](#development)
- [Roadmap](#roadmap)
- [Star History](#star-history)
- [Activity](#activity)
- [Contributors](#contributors)
- [License](#license)

## Overview

FIM Agent is a provider-agnostic Python framework for building AI agents that dynamically plan and execute complex tasks. What makes it different is the **Connector Hub** architecture — three delivery modes, one agent core:

| Mode | What it is | How you access it |
|------|-----------|------------------|
| **Standalone** | General-purpose AI assistant — search, code, knowledge base | Portal |
| **Copilot** | AI embedded in a host system — works alongside users in their existing UI | iframe / widget / embed into host pages |
| **Hub** | Central AI orchestration — all your systems connected, cross-system intelligence | Portal / API |

```
            ┌──────────────────────────┐
 ERP ──────►│                          │◄────── CRM
 Database ──►│    FIM Agent Hub         │◄────── OA
 Lark ──────►│    (AI orchestration)   │◄────── Custom API
            └──────────────────────────┘
```

The core is always the same: ReAct reasoning loops, dynamic DAG planning with concurrent execution, pluggable tools, and a protocol-first architecture with zero vendor lock-in.

## Why FIM Agent

### Land and Expand

Start by embedding a **Copilot** into one system — say, your ERP. Users interact with AI right inside their familiar interface: query financial data, generate reports, get answers without leaving the page.

When the value is proven, set up a **Hub** — a central portal that connects all your systems together. The ERP Copilot keeps running embedded; the Hub adds cross-system orchestration: query contracts in CRM, check approvals in OA, notify stakeholders on Lark — all from one place.

Copilot proves value inside one system. Hub unlocks value across all systems.

### What FIM Agent Does NOT Do

FIM Agent does not replicate workflow logic that already exists in your target systems:

- **No BPM/FSM engine** — Approval chains, routing, escalation, and state machines are the target system's responsibility. These systems spent years building this logic.
- **No drag-and-drop workflow editor** — Use Dify if you need visual flowcharts. FIM Agent's DAG planner generates execution graphs dynamically.
- **Connector = API call** — From the connector's perspective, "transfer approval" = one API call, "reject with reason" = one API call. All complex workflow operations collapse to HTTP requests. FIM Agent calls the API; the target system manages the state.

This is a deliberate architectural boundary, not a capability gap.

### Competitive Positioning

|  | Dify | Manus | Coze | FIM Agent |
|--|------|-------|------|-----------|
| **Approach** | Visual workflow builder | Autonomous agent | Builder + agent space | AI Connector Hub |
| **Planning** | Human-designed static DAGs | Multi-agent CoT | Static + dynamic | LLM DAG planning + ReAct |
| **Cross-system** | API nodes (manual) | No | Plugin marketplace | Hub Mode (N:N orchestration) |
| **Human Confirmation** | No | No | No | Yes (pre-execution gate) |
| **Self-hosted** | Yes (Docker stack) | No | Yes (Coze Studio) | Yes (single process) |

> Deep dive: [Philosophy](https://github.com/fim-ai/fim-agent/wiki/Philosophy) | [Execution Modes](https://github.com/fim-ai/fim-agent/wiki/Execution-Modes) | [Competitive Landscape](https://github.com/fim-ai/fim-agent/wiki/Competitive-Landscape)

### Where FIM Agent Sits

```
                Static Execution          Dynamic Execution
            ┌──────────────────────┬──────────────────────┐
 Static     │ BPM / Workflow       │ ACM                  │
 Planning   │ Camunda, Activiti    │ (Salesforce Case)    │
            │ Dify, n8n, Coze     │                      │
            ├──────────────────────┼──────────────────────┤
 Dynamic    │ (transitional —      │ Autonomous Agent     │
 Planning   │  unstable quadrant)  │ AutoGPT, Manus       │
            │                      │ ★ FIM Agent (bounded)│
            └──────────────────────┴──────────────────────┘
```

Dify/n8n are **Static Planning + Static Execution** — humans design the DAG on a visual canvas, nodes execute fixed operations. FIM Agent is **Dynamic Planning + Dynamic Execution** — LLM generates the DAG at runtime, each node runs a ReAct loop, with re-planning when goals aren't met. But bounded (max 3 re-plan rounds, token budgets, confirmation gates), so more controlled than AutoGPT.

FIM Agent doesn't do BPM/FSM — workflow logic belongs to the target system, Connectors just call APIs.

> Full explanation: [Philosophy](https://github.com/fim-ai/fim-agent/wiki/Philosophy)

## Key Features

#### Connector Platform (the core)
- **Connector Hub Architecture** — Standalone assistant, embedded Copilot, or central Hub — same agent core, different delivery.
- **Any System, One Pattern** — Connect APIs, databases, and message buses. Actions auto-register as agent tools with auth injection (Bearer, API Key, Basic).
- **AI-Assisted Builder** — Import OpenAPI specs (YAML/JSON/URL) to auto-generate connectors and actions. AI chat panel for natural language action generation and refinement.

#### Intelligent Planning & Execution
- **Dynamic DAG Planning** — LLM decomposes goals into dependency graphs at runtime. No hard-coded workflows.
- **Concurrent Execution** — Independent steps run in parallel via asyncio.
- **DAG Re-Planning** — Auto-revises the plan up to 3 rounds when goals aren't met.
- **ReAct Agent** — Structured reasoning-and-acting loop with automatic error recovery.

#### Tools & Integrations
- **Pluggable Tool System** — Auto-discovery; ships with Python executor, Node.js executor, calculator, web search/fetch, HTTP request, shell exec, and more.
- **Pluggable Sandbox** — `python_exec` / `node_exec` / `shell_exec` run in local or Docker mode (`CODE_EXEC_BACKEND=docker`) for OS-level isolation (`--network=none`, `--memory=256m`). Safe for SaaS and multi-tenant deployments.
- **MCP Protocol** — Connect any MCP server as tools. Third-party MCP ecosystem works out of the box.
- **OpenAI-Compatible** — Works with any `/v1/chat/completions` provider (OpenAI, DeepSeek, Qwen, Ollama, vLLM…).

#### RAG & Knowledge
- **Full RAG Pipeline** — Jina embedding + LanceDB + FTS + RRF hybrid retrieval + reranker. Supports PDF, DOCX, Markdown, HTML, CSV.
- **Grounded Generation** — Evidence-anchored RAG with inline `[N]` citations, conflict detection, and explainable confidence scores.
- **KB Document Management** — Chunk-level CRUD, text search across chunks, failed document retry, and auto-migrating vector store schema.

#### Portal & UX
- **Real-time Streaming** — SSE with KaTeX math rendering and tool step folding.
- **DAG Visualization** — Interactive flow graph with live status, dependency edges, and click-to-scroll.
- **Conversational Interrupt** — Send follow-up messages while the agent is running; injected at the next iteration boundary.
- **Dark / Light / System Theme** — Full theme support with system-preference detection.
- **Command Palette** — Conversation search, starring, batch operations, and title rename.

#### Platform & Multi-Tenant
- **JWT Auth** — Token-based SSE auth, conversation ownership, per-user resource isolation.
- **Agent Management** — Create, configure, and publish agents with bound models, tools, and instructions. Per-agent execution mode (Standard/Planner) and temperature control.
- **Admin Panel** — System stats dashboard (users, conversations, tokens, model usage charts, tokens-by-agent breakdown), connector call metrics (success rate, latency, call counts), user management with search/pagination, role toggle, password reset, and account enable/disable.
- **First-Run Setup Wizard** — On first launch, the portal guides you through creating an admin account (username, password, email). This one-time setup becomes your login credential — no config files needed.
- **Personal Center** — Per-user global system instructions, applied across all conversations.
- **Language Preference** — Per-user language setting (auto/en/zh) that directs all LLM responses to the chosen language.

#### Context & Memory
- **LLM Compact** — Automatic LLM-powered summarization to stay within token budgets.
- **ContextGuard + Pinned Messages** — Token budget manager; pinned messages are protected from compaction.
- **Single-Process Deployment** — No Redis, no PostgreSQL, no message queue. One process + SQLite.

## Architecture

### Connector Hub

```
                        ┌───────────────────────────┐
                        │     FIM Agent Hub          │
                        │                            │
 ERP (SAP/Kingdee) ────│►  Agent A: Finance Audit   │───► Lark / Slack
 CRM (Salesforce)  ────│►  Agent B: Contract Review  │───► Email / WeCom
 OA (Seeyon/Weaver) ───│►  Agent C: Approval Assist  │───► Teams / Webhook
 Custom DB (PG/MySQL) ──│►  Agent D: Data Reporting   │───► Any API
                        │                            │
                        └───────────────────────────┘
                              Portal / API / iframe
```

Each connector is a standardized bridge — the agent doesn't know or care whether it's talking to SAP or a custom PostgreSQL database. See [Connector Architecture](https://github.com/fim-ai/fim-agent/wiki/Connector-Architecture) for details.

### Internal Execution

FIM Agent provides two execution modes:

| Mode | Best for | How it works |
|------|----------|-------------|
| ReAct | Single complex queries | Reason → Act → Observe loop with tools |
| DAG Planning | Multi-step parallel tasks | LLM generates dependency graph, independent steps run concurrently |

```
User Query
    |
    v
+--------------+
|  DAG Planner |  LLM decomposes the goal into steps + dependency edges
+--------------+
    |
    v
+--------------+
| DAG Executor |  Launches independent steps concurrently (asyncio)
|              |  Each step is handled by a ReAct Agent
+--------------+
    |                         +-------+
    +--- ReAct Agent [1] ---> | Tools |  (python_exec, custom, ...)
    |                         +-------+
    +--- ReAct Agent [2] ---> | RAG   |  (retriever interface)
    |                         +-------+
    +--- ReAct Agent [N] ---> | ...   |
    |
    v
+---------------+
| Plan Analyzer |  LLM evaluates results; re-plans if goal not met
+---------------+
    |
    v
 Final Answer
```

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 18+ and pnpm (for the portal frontend)

### Get Running

```bash
git clone https://github.com/fim-ai/fim-agent.git
cd fim-agent

# Configure — only LLM_API_KEY is required
cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

# Install
uv sync --extra web
cd frontend && pnpm install && cd ..

# Launch
./start.sh
```

Open http://localhost:3000 — on first launch you'll be guided through creating an admin account (username, password, email). That's it — you're in.

### start.sh Commands

| Command          | What starts                                             | URL                                      |
| ---------------- | ------------------------------------------------------- | ---------------------------------------- |
| `./start.sh`     | Next.js + FastAPI                                       | http://localhost:3000 (UI) + :8000 (API) |
| `./start.sh dev` | Same, with hot reload (Python `--reload` + Next.js HMR) | Same                                     |
| `./start.sh api` | FastAPI only (headless, for integration or testing)     | http://localhost:8000/api                |

> **Docker deployment** is on the [Roadmap](https://github.com/fim-ai/fim-agent/wiki/Roadmap) (v0.9). For now, `./start.sh` is the recommended way to run.

The portal offers two modes: **ReAct Agent** (single-query tool loop) and **DAG Planner** (multi-step planning with concurrent execution), with real-time SSE streaming, DAG visualization, and KaTeX math rendering.

## Configuration

### Recommended Setup

Two API keys unlock all features:

| Service                | What it powers                        | Get a key                                               |
| ---------------------- | ------------------------------------- | ------------------------------------------------------- |
| **Anthropic** (Claude) | Agent reasoning (main LLM)            | [console.anthropic.com](https://console.anthropic.com/) |
| **Jina AI**            | Web search/fetch, embedding, reranker | [jina.ai](https://jina.ai/) (free tier available)       |

Minimal `.env` to get everything working:

```bash
LLM_API_KEY=sk-ant-...          # Anthropic API key
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_MODEL=claude-sonnet-4-6

JINA_API_KEY=jina_...           # Unlocks web tools + RAG
```

> Any OpenAI-compatible provider works (DeepSeek, Qwen, Ollama, vLLM, etc.) — just change `LLM_BASE_URL` and `LLM_MODEL`.

### All Variables

| Variable                     | Required | Default                                   | Description                                         |
| ---------------------------- | -------- | ----------------------------------------- | --------------------------------------------------- |
| `LLM_API_KEY`                | Yes      |                                           | API key for the LLM provider                        |
| `LLM_BASE_URL`               | No       | `https://api.openai.com/v1`               | Base URL of the OpenAI-compatible API               |
| `LLM_MODEL`                  | No       | `gpt-4o`                                  | Model identifier to use                             |
| `LLM_TEMPERATURE`            | No       | `0.7`                                     | Default sampling temperature                        |
| `LLM_CONTEXT_SIZE`           | No       | `128000`                                  | Context window size for the main LLM                |
| `LLM_MAX_OUTPUT_TOKENS`      | No       | `64000`                                   | Max output tokens per call for the main LLM         |
| `FAST_LLM_CONTEXT_SIZE`      | No       | *(falls back to `LLM_CONTEXT_SIZE`)*      | Context window size for the fast LLM                |
| `FAST_LLM_MAX_OUTPUT_TOKENS` | No       | *(falls back to `LLM_MAX_OUTPUT_TOKENS`)* | Max output tokens per call for the fast LLM         |
| `MAX_CONCURRENCY`            | No       | `5`                                       | Max parallel steps in DAG executor                  |
| `JINA_API_KEY`               | No       |                                           | Jina API key for embedding, reranker, and web tools |
| `EMBEDDING_MODEL`            | No       | `jina-embeddings-v3`                      | Embedding model identifier                          |
| `EMBEDDING_DIMENSION`        | No       | `1024`                                    | Embedding vector dimension                          |
| `RERANKER_MODEL`             | No       | `jina-reranker-v2-base-multilingual`      | Reranker model identifier                           |
| `VECTOR_STORE_DIR`           | No       | `./data/vector_store`                     | Directory for LanceDB vector store data             |

Copy `example.env` to `.env` and fill in your values:

```bash
cp example.env .env
```

## Development

```bash
# Install all dependencies (including dev extras)
uv sync --all-extras

# Run tests
pytest

# Run tests with coverage
pytest --cov=fim_agent --cov-report=term-missing

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## Roadmap

See the full [Roadmap](https://github.com/fim-ai/fim-agent/wiki/Roadmap) for version history and what's next.

Contributions and ideas are welcome. Open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

## Star History

<a href="https://star-history.com/#fim-ai/fim-agent&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-agent&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-agent&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-agent&type=Date" />
  </picture>
</a>

## Activity

![Alt](https://repobeats.axiom.co/api/embed/ff8b449fd183a323d81f33fc96e7ce42ad745f65.svg "Repobeats analytics image")

## Contributors

[![Contributors](https://contrib.rocks/image?repo=fim-ai/fim-agent)](https://github.com/fim-ai/fim-agent/graphs/contributors)

## License

FIM Agent Source Available License. This is **not** an OSI-approved open source license.

**Permitted**: internal use, modification, distribution with license intact, embedding in your own (non-competing) applications.

**Restricted**: multi-tenant SaaS, competing agent platforms, white-labeling, removing branding.

For commercial licensing inquiries, please open an issue on [GitHub](https://github.com/fim-ai/fim-agent).

See [LICENSE](LICENSE) for full terms.

---

<div align="center">

🌐 [Website](https://agent.fim.ai/) · 📖 [Wiki](https://github.com/fim-ai/fim-agent/wiki) · 📋 [Changelog](https://github.com/fim-ai/fim-agent/wiki/CHANGELOG) · 🐛 [Report Bug](https://github.com/fim-ai/fim-agent/issues) · 💡 [Request Feature](https://github.com/fim-ai/fim-agent/issues) · 🐦 [Twitter](https://x.com/FIM_TECH)

</div>

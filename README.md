<div align="center">

![FIM Agent Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![GitHub stars](https://img.shields.io/github/stars/fim-ai/fim-agent?style=social)](https://github.com/fim-ai/fim-agent/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/fim-ai/fim-agent?style=social)](https://github.com/fim-ai/fim-agent/network)
[![GitHub issues](https://img.shields.io/github/issues/fim-ai/fim-agent)](https://github.com/fim-ai/fim-agent/issues)

**Provider-agnostic Agent Platform: from standalone AI assistant to embeddable runtime that modernizes legacy systems.**

</div>

---

## Table of Contents

- [Overview](#overview)
- [Why FIM Agent](#why-fim-agent)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Development](#development)
- [Roadmap](#roadmap)
- [Star History](#star-history)
- [Contributors](#contributors)
- [License](#license)

## Overview

FIM Agent is a provider-agnostic Python framework for building AI agents that dynamically plan and execute complex tasks. It operates in two modes:

- **Standalone (Portal)**: A full-featured AI assistant with dynamic DAG planning, concurrent execution, and real-time streaming. The LLM decomposes goals into dependency-aware DAGs at runtime, runs independent steps in parallel, and re-plans if needed.
- **Sidecar (Embedded Engine)**: An embeddable runtime that proactively bridges into legacy systems, reading their databases, calling their APIs, and pushing notifications, all without requiring a single line of code change on the host side.

Both modes share the same agent core: ReAct reasoning loops, pluggable tools, and a protocol-first architecture with zero vendor lock-in.

## Why FIM Agent

Enterprise clients don't want "another system to maintain". Their legacy systems (ERP such as SAP, Kingdee/金蝶, Yonyou/用友; CRM such as Salesforce, Fanruan/帆软; OA such as Seeyon/致远, Weaver/泛微; finance; HR) are often **frozen**: untouchable codebases with decades of business logic baked in.

FIM Agent solves this with two integration directions:

```
┌─ If they CAN'T modify their system (90% of cases) ───────────────┐
│                                                                    │
│  FIM Agent ──→ reads their DB directly (bypass app layer)         │
│            ──→ calls their existing APIs / RPCs                   │
│            ──→ pushes results to DingTalk (钉钉) / WeCom (企微)   │
│                 / Slack / Teams / email                            │
│            ──→ writes back via DB or API when authorized          │
│                                                                    │
│  Zero code change on their side. Agent = active "digital worker". │
└────────────────────────────────────────────────────────────────────┘

┌─ If they CAN modify their system (bonus) ─────────────────────────┐
│                                                                    │
│  Their system ──→ calls FIM Agent API (like calling Dify)         │
│  FIM Agent exposes: /api/execute, /api/stream, /api/kb            │
│                                                                    │
│  Standard API integration. We expose, they consume.               │
└────────────────────────────────────────────────────────────────────┘
```

**vs Dify / n8n**: Static workflow engines that require the host system to call *their* API. If the host can't be modified, the project stalls. FIM Agent goes the other direction: the agent reaches into the host.

**vs Manus / AutoGPT**: Single-use autonomous agents with no platform layer. FIM Agent adds multi-tenant management, persistent conversations, knowledge bases, and an Adapter protocol that standardizes how agents connect to external systems.

> Deep dive: [Philosophy](https://github.com/fim-ai/fim-agent/wiki/Philosophy) | [Execution Modes](https://github.com/fim-ai/fim-agent/wiki/Execution-Modes) | [Planning Landscape](https://github.com/fim-ai/fim-agent/wiki/Planning-Landscape)

## Key Features

- **Dynamic DAG Planning**: The LLM decomposes goals into dependency graphs at runtime with tool-name-aware planning (planner receives the available tool list to constrain `tool_hint`). No hard-coded workflows.
- **DAG Visualization**: Interactive flow graph (@xyflow/react) in an expand/collapse right sidebar with real-time step status, dependency edges, click-to-scroll navigation, and auto fitView. ReAct mode shows a compact step timeline.
- **DAG Re-Planning**: When the goal is not achieved, the planner automatically revises the plan using step results as context and retries, up to 3 rounds.
- **Concurrent Execution**: Independent DAG steps run in parallel via `asyncio`, bounded by a configurable concurrency limit.
- **Real-time Streaming**: Portal streams reasoning steps and tool calls as they happen via SSE, with KaTeX math rendering support.
- **ReAct Agent**: Structured reasoning-and-acting loop with JSON-based tool calls, automatic error recovery, and iteration limits.
- **OpenAI-Compatible**: Works with any provider exposing the `/v1/chat/completions` interface (OpenAI, DeepSeek, Qwen, Ollama, vLLM, and others).
- **Pluggable Tool System**: Protocol-based tool interface with auto-discovery. Ships with Python executor, calculator, file ops, web search/fetch (Jina), HTTP request (any REST API), and sandboxed shell exec (curl, jq, etc.).
- **Multi-Tenant Platform**: JWT auth with token-based SSE authentication, conversation ownership validation, per-user resource isolation. Conversation starring, batch delete, title rename, and Cmd+K chat search command palette.
- **Agent Management**: Create, configure, and publish agents with bound LLM model, tool categories, and custom instructions. Chat endpoints automatically resolve agent config.
- **LLM Compact**: Automatic LLM-powered summarisation of long conversation histories to stay within token budgets without losing context.
- **ContextGuard + Pinned Messages**: Unified context window budget manager -- checks token counts against model limits, applies hint-specific LLM compaction (ReAct iteration, planner input, step dependency), truncates oversized messages, and falls back to smart truncation. Pinned messages (task descriptions, critical context) are protected from compaction so agents never "forget what they're doing" during long tool-call loops. Configurable via `LLM_CONTEXT_SIZE` / `LLM_MAX_OUTPUT_TOKENS` env vars.
- **RAG & Knowledge Base**: Full RAG pipeline -- Jina embedding + LanceDB vector store + native FTS + RRF hybrid retrieval + Jina reranker. Document loaders for PDF, DOCX, Markdown, HTML, CSV. Fixed-size, recursive, and semantic chunking. KB management UI with document upload, background ingest, and Markdown document creation. KB detail page with document table and chunk browser supporting chunk-level CRUD (view, edit, delete individual chunks). Agent `kb_retrieve` tool for autonomous knowledge lookup.
- **Grounded Generation**: Evidence-anchored RAG with claim-level citations. When an agent is bound to knowledge bases, retrieval automatically upgrades to `grounded_retrieve` -- a 5-stage pipeline (multi-KB parallel retrieval, LLM citation extraction, query-chunk alignment scoring, cross-document conflict detection, score-based confidence). Produces inline `[N]` citation markers linked to a structured References section (confidence badge, source names, page numbers, exact quotes), detects contradictions between sources, and computes explainable confidence scores (no LLM self-evaluation). Agent form UI supports KB multi-select binding.
- **Minimal Dependencies**: Only three runtime dependencies: `openai`, `httpx`, `pydantic`.

## Architecture

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

Open http://localhost:3000 — that's it.

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

[![Star History Chart](https://api.star-history.com/svg?repos=fim-ai/fim-agent&type=Date)](https://star-history.com/#fim-ai/fim-agent&Date)

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

[Report Bug](https://github.com/fim-ai/fim-agent/issues) · [Request Feature](https://github.com/fim-ai/fim-agent/issues) · [Wiki](https://github.com/fim-ai/fim-agent/wiki)

</div>

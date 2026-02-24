# Roadmap

> Goal: Build a **provider-agnostic Agent Platform** -- from standalone AI assistant to embeddable runtime that modernizes legacy systems.
>
> Principles: **Provider-agnostic** (no vendor lock-in), **minimal-abstraction**, **protocol-first**, **dual-mode** (standalone + embedded).

## Delivery Modes (per-project)

| Mode | Description |
|------|-------------|
| **Web UI** | Platform's built-in interface (standalone assistant) |
| **iframe / standalone URL** | Embed into host system pages, authenticate and operate host APIs/databases |
| **API** | Pure HTTP/SSE interface for programmatic access |

---

### v0.1 -- Foundation (shipped)

- [x] **ReAct Agent**: JSON-mode reasoning and action loop
- [x] **DAG Planning**: DAGPlanner -> DAGExecutor -> PlanAnalyzer with concurrent step execution
- [x] **Next.js Portal**: Interactive playground with real-time SSE streaming
- [x] **Streaming Output**: Async queue bridge and `on_iteration` callbacks for both ReAct and DAG modes
- [x] **KaTeX Math Rendering**: LaTeX math display in frontend

### v0.2 -- Core Enhancements (shipped)

- [x] **Native Function Calling**: Support OpenAI-style `tool_choice` / `parallel_tool_calls` alongside the ReAct JSON mode
- [x] **Streaming Agent Output**: ~~Yield intermediate reasoning and tool results as they happen via `AsyncIterator`~~ -- Shipped in v0.1: real-time SSE streaming with async queue bridge and `on_iteration` callbacks for both ReAct and DAG modes
- [x] **Conversation Memory**: Short-term message window + conversation summary for multi-turn agent sessions
- [x] **Retry & Rate Limiting**: Configurable retry with exponential backoff + token-bucket rate limiter respecting provider quotas
- [x] **Token Usage Tracking**: Count and aggregate prompt / completion tokens across all LLM calls; expose per-task cost summary
- [x] **Multi-Model Support**: ModelRegistry to configure multiple LLMs per project (general / fast / vision / compact), switch per step

### v0.3 -- Rich Tool Ecosystem

> *"Let the Agent have hands and feet"*
>
> Universal capabilities first -- this is the foundation for everything that follows.

- [x] **Web Search Tool**: Jina-powered web search (`s.jina.ai`) with env-driven API key
- [x] **Web Fetch Tool**: Jina-powered HTTP reader (`r.jina.ai`) for clean page content extraction
- [x] **DAG Visualization**: Interactive @xyflow/react flow graph with real-time step status (pending/running/completed/failed), dependency edges, duration, and iteration details
- [x] **Right Sidebar Panel**: Persistent right sidebar with expand/collapse toggle (layout flips between ~8/2 and ~2/8 ratio), auto fitView on resize; DAG flow graph + ReAct compact timeline; click-to-scroll from sidebar to main content; responsive (auto-hides < 1024px)
- [x] **Env-Driven Configuration**: `LLM_TEMPERATURE` and `MAX_CONCURRENCY` configurable via environment variables
- [x] **Parallel Tool Timing Fix**: Correct elapsed-time measurement for concurrent tool calls in native function-calling mode
- [ ] **Built-in Tools (remaining)**: Calculator, file ops, code execution sandbox
- [ ] **MCP Client**: Model Context Protocol integration for dynamic tool discovery and invocation via standardized servers
- [ ] **Tool Auto-Discovery**: Convention-based loading (`get_{name}_tool()`) so new tools register without manual wiring
- [ ] **Tool Categories & Permissions**: Group tools by category, enable/disable per agent

### v0.4 -- Platform Foundation

> *"From playground to a real platform"*
>
> Multi-tenancy, persistence, agent management -- supports both standalone usage and the adapter model that comes later.

- [ ] **Persistent Storage**: SQLAlchemy ORM + SQLite (tasks, agents, conversations, model configs)
- [ ] **Multi-Tenant**: User registration/login (JWT), workspace isolation
- [ ] **Project & Agent Management**: Create/configure/publish agents; bind tools, model, and prompt per project
- [ ] **Conversation Persistence**: Session history stored durably, session resume across restarts
- [ ] **File Management**: Upload/download/associate files with tasks and agents

### v0.5 -- RAG & Knowledge

> *"Give the Agent memory and knowledge"*

- [ ] **Embedding**: `BaseEmbedding` protocol + OpenAI-compatible implementation
- [ ] **Vector Store**: LanceDB embedded vector store -- zero external services
- [ ] **Document Loaders**: PDF, DOCX, Markdown, HTML, CSV
- [ ] **Chunking Strategies**: Fixed-size, recursive, and semantic chunking
- [ ] **Hybrid Retrieval**: Dense vector search + BM25 full-text search
- [ ] **Knowledge Base Management**: Per-project knowledge base CRUD

### v0.6 -- System Adapter Protocol + Zhihe Integration

> *"The standard protocol for connecting legacy systems"*
>
> The universal platform is ready. Now add adaptation capabilities. Zhihe contract system is the first real-world target.

- [ ] **BaseAdapter Protocol**: `connect()`, `get_capabilities()`, `get_tools()`, `OperationDescriptor` (with `read_only` flag)
- [ ] **HTTP API Adapter**: Generic REST API connector (httpx-based)
- [ ] **Database Adapter**: Read-only SQL query tool (async, parameterized queries)
- [ ] **Auth Passthrough**: Proxy host-system authentication; agent acts on behalf of the logged-in user
- [ ] **Zhihe Contract Adapter**: First concrete implementation -- search / detail / compare / timeline / statistics
- [ ] **Operation Audit Log**: Every tool call recorded (timestamp, user, tool, params, result)

### v0.7 -- Human Confirmation + Embeddable UI

> *"Safely operate legacy systems + injectable delivery"*

- [ ] **Confirmation Gate**: Write operations require human approval; SSE event `confirmation_required`; configurable policy
- [ ] **Dry-run Mode**: Preview operation effects without actual execution
- [ ] **Zhihe Write Operations**: Approval, commenting, status changes (all require confirmation)
- [ ] **Embeddable Widget**: Lightweight JS bundle (<100KB), inject via `<script>` tag into host pages
- [ ] **Page Context Injection**: Read current context from host page (contract ID, page URL, DOM selector)
- [ ] **iframe / Standalone URL Mode**: Authenticated standalone access for embedding

### v0.8 -- Declarative Adapter + Multi-System

> *"From writing code to filling config" (Standardization Level 2)*

- [ ] **Declarative Adapter Engine**: Define adapters in YAML/JSON, auto-generate Tools
- [ ] **Adapter Testing Harness**: Mock host system for testing without a live connection
- [ ] **Schema Introspection**: Runtime adapter self-description capabilities
- [ ] **Multi-Adapter Routing**: Single instance connects to multiple host systems; agent auto-routes
- [ ] **Second Adapter**: Adapt a second system to validate protocol generality
- [ ] **`create_adapter` CLI**: Scaffolding tool for new adapter projects

### v0.9 -- Observability + Production

> *"See what the Agent is doing"*

- [ ] **OpenTelemetry Tracing**: Full-chain tracing for LLM calls, tool execution, and adapter invocations
- [ ] **Cost Dashboard**: Token usage aggregation by user / project / agent / model
- [ ] **Circuit Breaker**: Adapter connection circuit breaker with graceful degradation
- [ ] **Health Check**: `/api/health` endpoint (LLM + adapter + vector store connectivity)
- [ ] **Execution Replay**: Complete execution trace replay for debugging and auditing
- [ ] **Docker Compose**: API + SQLite + optional Langfuse, production-ready

### v1.0 -- Enterprise & Scale

> *"Enterprise-ready"*

- [ ] **AI Adapter Generation**: Upload Swagger/OpenAPI spec -> auto-generate adapter config (Standardization Level 3)
- [ ] **Plugin System**: Pip-installable adapter packages with entry-point auto-registration
- [ ] **Admin Dashboard**: Audit logs, adapter management, health monitoring, usage/cost analytics
- [ ] **Multi-Agent**: Nested agents, specialized roles (researcher / reviewer / actor)
- [ ] **Scheduled Jobs**: Cron triggers, webhook triggers
- [ ] **Enterprise Security**: Data encryption, IP whitelisting, SOC2 audit logging
- [ ] **PostgreSQL**: Optional for large-scale deployments
- [ ] **i18n**: Chinese / English

---

## Adapter Standardization Levels

| Level | Version | Approach |
|-------|---------|----------|
| **Level 1** | v0.6 | Python code: write a class extending `BaseAdapter` |
| **Level 2** | v0.8 | Declarative: YAML/JSON config, zero Python code |
| **Level 3** | v1.0 | AI-generated: upload OpenAPI spec, auto-generate config |

## Multi-Tenant Model

```
Platform (multi-tenant)
├── Tenant A
│   ├── Project 1: Zhihe Contract Sidecar [adapter: zhihe + built-in tools]
│   ├── Project 2: OA Assistant [adapter: oa]
│   └── Project 3: General AI Assistant [tools: search, browser, code]
├── Tenant B
│   └── Project 1: Online Agent [tools: all built-in + MCP]
```

## Migration from Old Roadmap

| Old Version | Old Content | Disposition | Rationale |
|-------------|-------------|-------------|-----------|
| v0.3 Tool Ecosystem | Built-in tools, MCP | **Kept** at v0.3 | Universal tools are the foundation |
| v0.4 RAG | Vector store, doc loaders | **Moved** to v0.5 | Platform foundation comes first |
| v0.5 Agent Builder | Visual agent creation | **Simplified** -> v0.4 Project/Agent management | Code + config over drag-and-drop |
| v0.6 Multi-Agent + HITL | Nested agents, approval gates | **Split** | HITL -> v0.7; Multi-Agent -> v1.0 |
| v0.7 Production Platform | User management, persistence | **Moved earlier** to v0.4 | Platform basics needed sooner |
| v0.8 Observability | Tracing, cost, monitoring | **Moved** to v0.9 | Adapter features take priority |
| v0.9 Workflow Engine | Dify-style visual orchestration | **Removed** | Not pursuing Dify parity |
| -- | -- | **New** v0.6 System Adapter | Core differentiator |
| -- | -- | **New** v0.7 Embeddable UI | Sidecar delivery mode |
| -- | -- | **New** v0.8 Declarative Adapter | Standardization at scale |

---

Contributions and ideas are welcome -- open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

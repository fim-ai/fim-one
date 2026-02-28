# Roadmap

> Goal: Build a **provider-agnostic Agent Platform** -- from standalone AI assistant to embeddable runtime that modernizes legacy systems.
>
> Principles: **Provider-agnostic** (no vendor lock-in), **minimal-abstraction**, **protocol-first**, **dual-mode** (standalone + embedded).

## Product Vision

FIM Agent is an **AI Agent engine** that serves three progressive layers:

```
Layer 1 — Self-use        : Your own AI assistant (Portal mode)
Layer 2 — Dify alternative : Deploy as a standalone Agent platform for clients
Layer 3 — Sidecar engine  : Embed into enterprise legacy systems as invisible AI infrastructure
```

**Layer 3 is the core differentiator.** Enterprise clients have frozen legacy systems -- ERP, CRM, OA, finance, HR -- that cannot be modified. FIM Agent bridges into these systems proactively:

```
                        ┌───────────────────────────┐
                        │     FIM Agent Engine       │
                        │                           │
                        │  Agent A: Finance Audit    │──→ DB Adapter  ──→ SAP / Kingdee (金蝶) (Oracle/PG)
                        │  Agent B: Contract Review  │──→ API Adapter ──→ CRM / CLM system (REST)
                        │  Agent C: Notification     │──→ Msg Adapter ──→ DingTalk (钉钉) / Slack / Teams
                        │  Agent D: Data Reporting   │──→ DB Adapter  ──→ Business DB (MySQL/PG)
                        │                           │
                        └──────────┬────────────────┘
                                   │
                   ┌───────────────┼───────────────┐
                   │               │               │
              Portal (UI)    API (headless)    iframe (embed)
              standalone      sidecar mode     injected into
              deployment      for integration  host pages
```

**Two integration directions:**

| Direction | When | How |
|-----------|------|-----|
| **Agent → Host** (active) | Host system can't be modified (90% of cases) | Agent reads DB / calls API / pushes messages directly |
| **Host → Agent** (passive) | Host system can be modified | Host calls FIM Agent's `/api/execute` like calling Dify |

## Delivery Modes (per-project)

| Mode | Description |
|------|-------------|
| **Portal (Web UI)** | Platform's built-in interface (standalone assistant) |
| **API (headless)** | Pure HTTP/SSE interface; no UI required; for sidecar and programmatic access |
| **iframe / standalone URL** | Embed into host system pages, authenticate and operate host APIs/databases |

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
- [x] **Calculator Tool**: Safe AST-based math expression evaluation (no eval/exec)
- [x] **File Ops Tool**: Sandboxed file read/write/list/mkdir in workspace directory with path traversal protection
- [x] **Python Exec Sandbox**: Restricted builtins whitelist, blocked dangerous modules (subprocess, shutil, ctypes), stderr capture, 100KB output limit
- [x] **Tool Auto-Discovery**: Convention-based loading via `discover_builtin_tools()` — drop a `BaseTool` subclass in `builtin/` and it auto-registers
- [x] **MCP Client**: Model Context Protocol integration — connect to MCP servers via stdio transport, auto-discover tools, register as native Tool adapters; configured via `MCP_SERVERS` env var
- [x] **Tool Categories & Permissions**: Tools tagged with `category` property; `ToolRegistry` supports `filter_by_category()`, `exclude_by_name()`, and category-filtered `to_openai_tools()`
- [x] **DAG Node Detail Enhancement**: Display assigned tool list, start time, and elapsed duration on each DAG step node
- [x] **Frontend Token Usage**: Show per-task input/output token counts in the chat UI (backend tracking shipped in v0.2)

### v0.4 -- Platform Foundation

> *"From playground to a real platform"*
>
> Multi-tenancy, persistence, agent management -- supports both standalone usage and the adapter model that comes later.

- [x] **HTTP Request Tool**: General-purpose HTTP client (`http_request`) — supports GET/POST/PUT/PATCH/DELETE with custom headers, query params, and body; SSRF protection (private IP blocking), JSON auto-pretty-print, 200KB response limit
- [x] **Shell Exec Tool**: Sandboxed shell execution (`shell_exec`) — run curl, jq, awk, grep etc. with command blocklist (30+ patterns), env var scrubbing, system path write protection, per-user sandbox directory
- [x] **Persistent Storage**: SQLAlchemy ORM + SQLite (conversations, messages, model configs); async engine with WAL mode and StaticPool for concurrency
- [x] **Conversation Persistence**: Session history stored durably; multi-turn context via `DbMemory` — loads prior turns from DB, smart truncation with CJK-aware token estimation, auto-compact within token budget; both ReAct and DAG modes supported
- [x] **Multi-Tenant**: User registration/login (JWT), SSE token-based auth, conversation ownership validation
- [x] **Project & Agent Management**: Create/configure/publish agents; bind tools, model, and prompt per agent; agent-aware chat endpoints resolve LLM, tools, and instructions from agent config
- [x] **File Upload & Management**: Upload/download/delete files with per-user isolation; configurable upload directory

### v0.5 -- RAG, Knowledge & Memory

> *"Give the Agent memory and knowledge"*

**Memory & Compact**
- [x] **LLM Compact**: When conversation history exceeds threshold, use a fast LLM to compress early turns into a summary; retain recent turns verbatim; transparent to the agent
- [x] **ContextGuard**: Unified context window budget manager -- checks message token counts against model limits (`LLM_CONTEXT_SIZE` / `LLM_MAX_OUTPUT_TOKENS`), applies hint-specific LLM compaction (react_iteration, planner_input, step_dependency), truncates oversized messages, falls back to smart truncation; wired into ReAct agent and DAG executor
- [x] **Pinned Messages**: `ChatMessage.pinned` flag protects critical messages (task description, user corrections) from compaction — ContextGuard and CompactUtils perform three-way split (system / pinned / compactable), pinned messages never enter the "old" pool for summarisation; ReAct pins the initial user message; DAG steps receive `original_goal` context so agents don't "forget what they're doing" during long tool-call loops; also fixes model_hint agents missing `context_guard` and `extra_instructions`
- [ ] **Conversation Summary Memory**: Automatic rolling summaries that persist across long sessions; hybrid window + summary strategy
- [ ] **Semantic Memory Store**: Cross-conversation knowledge extraction and retrieval; agent remembers facts/preferences across sessions via embedding-based lookup
- [ ] **Memory Lifecycle**: TTL-based expiry, importance scoring, explicit forget/remember commands

**DAG Mode Feature Parity** *(avoid falling behind ReAct)*
- [ ] **DAG Multi-Turn Polish**: Currently injects history as text prefix to planner; upgrade to structured message history so planner can reason about prior tool results and plan evolution
- [ ] **DAG LLM Compact**: Apply LLM compact to the enriched query before planning; long conversation context can blow up planner input
- [x] **DAG Re-Planning**: When the analyzer determines the goal was not achieved (confidence < 0.5), the pipeline automatically re-plans using previous step results as context and retries, up to 3 rounds; new SSE phase event `replanning`; done payload includes `rounds` count
- [x] **Tool-Name-Aware Planning**: Planner `tool_hint` constrained to available tool names; prevents hallucinated tool references in generated plans
- [ ] **DAG Step-Level ReAct**: Each DAG step runs a full ReAct agent loop (multi-step reasoning + tool use) instead of a single LLM call; enables complex sub-tasks within a DAG node — the minimal viable unit of Multi-Agent capability, reusing existing DAG infrastructure
- [ ] **DAG Step-Level Memory**: Each step executor sees relevant prior conversation context, not just the step task description
- [ ] **DAG Streaming Improvements**: Stream planner reasoning (not just step progress); show plan changes in real-time when re-planning occurs
- [ ] **DAG History Replay**: Frontend replays persisted DAG executions with the flow graph (currently only ReAct timeline replays correctly)

**RAG & Knowledge Base**
- [x] **Embedding**: `BaseEmbedding` protocol + OpenAI-compatible implementation (Jina jina-embeddings-v3)
- [x] **Vector Store**: LanceDB embedded vector store -- zero external services, native FTS, content-hash dedup
- [x] **Document Loaders**: PDF, DOCX, Markdown, HTML, CSV, plain text
- [x] **Chunking Strategies**: Fixed-size, recursive, and semantic chunking
- [x] **Hybrid Retrieval**: Dense vector search + LanceDB native FTS + RRF fusion + Jina reranker
- [x] **Knowledge Base Management**: Full KB CRUD with document upload, background ingest, and agent `kb_retrieve` tool
- [x] **Grounded Generation**: Evidence-anchored RAG — when an agent is bound to KBs, `kb_retrieve` auto-upgrades to `grounded_retrieve` with 5-stage pipeline: multi-KB parallel retrieval, LLM citation extraction (exact quotes), query-chunk alignment scoring, cross-document conflict detection, and score-based confidence (pure math, no LLM self-eval). Agent model gains `kb_ids` + `grounding_config` fields; agent form UI supports KB multi-select binding; frontend evidence panel with collapsible citations and conflict warnings; inline `[N]` citation markers with structured References section (confidence badge, source names, page numbers, exact quotes)
- [x] **Chunk-Level CRUD**: View, edit, and delete individual chunks within a knowledge base document; inline chunk editor with content preview
- [x] **KB Detail Page**: Dedicated knowledge base detail page with document table and chunk browser; navigate from KB list to full document/chunk management
- [x] **Markdown Document Creation**: Create markdown documents directly from the KB UI; content authored in-browser and ingested into the knowledge base

**Conversation Management**
- [x] **Conversation Starring & Pinning**: Star/pin important conversations for quick access; starred conversations surface to the top of the sidebar
- [x] **Batch Delete Conversations**: Select and delete multiple conversations at once from the sidebar
- [x] **Chat Search (Cmd+K)**: Command palette–style search dialog for quickly finding conversations by title or content
- [x] **Conversation Rename**: Inline rename conversations directly from the sidebar

### v0.6 -- System Adapter & Sandbox Hardening

> *"The standard protocol for connecting legacy systems"*
>
> The universal platform is ready. Now add adaptation capabilities so FIM Agent can bridge into any host system without modifying it.
>
> **Core insight**: The host system can't be modified. Agent must proactively bridge in -- read their DB, call their API, push to their message bus.
>
> **Architecture**: Adapters are MCP Servers with a governance layer (Adapter SDK) on top. The agent sees adapters as ordinary tools -- zero coupling. See [Adapter Architecture](Adapter-Architecture) for the full design.

**Execution Sandbox Hardening**
- [ ] **Per-User Virtual Environment**: Each conversation spawns an isolated Python venv; user code executes via subprocess in the venv, not the host interpreter; prevents cross-session state leakage and module pollution
- [ ] **Resource Limits**: CPU time and memory caps via `resource.setrlimit()` (Unix) for both Python exec and shell exec; prevents OOM and infinite-loop DoS
- [ ] **File I/O Sandboxing**: Replace whitelisted `open()` in python_exec with path-validated wrapper; all file access confined to conversation sandbox directory
- [ ] **Shell Exec Allowlist Mode**: Optional switch from command blocklist (regex-bypassable) to strict allowlist (`curl`, `grep`, `jq`, `awk`, `sed`, etc.); defense-in-depth against variable expansion / command substitution bypass

**System Adapter**
- [ ] **Adapter SDK**: Governance layer on MCP -- `read_only` enforcement, operation classification, audit hooks, auth passthrough interface
- [ ] **Database Adapter**: Direct SQL read against host DB (async, parameterized, read-only by default); supports PostgreSQL, MySQL, Oracle, SQL Server
- [ ] **HTTP API Adapter**: Generic REST/SOAP connector (httpx-based); auto-discover endpoints from Swagger/WSDL when available
- [ ] **Message Push Adapter**: Send results to DingTalk (钉钉) / WeCom (企微) / Slack / Teams / email / webhook; templated message formatting
- [ ] **Auth Passthrough**: Proxy host-system authentication; agent acts on behalf of the logged-in user; scoped permissions (DB read-only vs API full-access) per adapter
- [ ] **Reference Adapter**: First concrete implementation targeting a real-world contract/business management system -- search / detail / compare / timeline / statistics
- [ ] **Operation Audit Log**: Every tool call recorded (timestamp, user, tool, params, result, source adapter)

**Example scenarios this unlocks:**
- **ERP / Finance** (SAP, Kingdee/金蝶, Oracle DB): Agent reads financial statements directly from DB, generates analysis reports, pushes to DingTalk (钉钉) / Slack
- **OA / Workflow** (Seeyon/致远, Weaver/泛微, REST API): Agent calls approval endpoints, AI-assisted classification and routing, writes results back
- **CRM / Contracts** (Salesforce, custom PG): Agent reads contract clauses, performs risk analysis, generates review opinions
- **Business DB** (MySQL / PG): Agent periodically scans for anomalies, generates alerts, notifies responsible parties via Teams / WeCom (企微) / email

### v0.7 -- Human Confirmation + Embeddable UI

> *"Safely operate legacy systems + injectable delivery"*

- [ ] **Confirmation Gate**: Write operations require human approval; SSE event `confirmation_required`; configurable policy
- [ ] **Dry-run Mode**: Preview operation effects without actual execution
- [ ] **Adapter Write Operations**: Approval, commenting, status changes on host system (all require confirmation)
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
- [ ] **Multi-Agent Orchestration** *(inspired by Claude Code Teams)*:
  - [ ] Agent Pool: Dynamic agent lifecycle — spawn on demand, shut down when done; each agent runs isolated ReAct loop with its own context window
  - [ ] Task Graph: `blockedBy` / `blocks` dependency edges between tasks; orchestrator dispatches newly-unblocked tasks as predecessors complete (dynamic DAG over message-passing)
  - [ ] Leader / Worker roles: Leader decomposes plan into tasks, assigns to Workers, monitors progress, re-assigns on failure
  - [ ] Inter-Agent Messaging: Direct messages + broadcast; Workers report results back to Leader
  - [ ] Graceful shutdown: Leader sends shutdown requests, Workers confirm or reject
  - [ ] Token economics awareness: Track per-agent token cost; expose total cost as `N agents × per-agent tokens`
- [ ] **Scheduled Jobs**: Cron triggers, webhook triggers
- [ ] **Enterprise Security**: Data encryption, IP whitelisting, SOC2 audit logging
- [ ] **PostgreSQL**: Optional for large-scale deployments
- [ ] **i18n**: Chinese / English

---

## Adapter Standardization Levels

| Level | Version | Approach |
|-------|---------|----------|
| **Level 1** | v0.6 | Python MCP Server with Adapter SDK governance |
| **Level 2** | v0.8 | Declarative YAML/JSON config, platform auto-generates MCP Server |
| **Level 3** | v1.0 | Upload OpenAPI/Swagger spec, AI generates config automatically |

## Multi-Tenant Model

```
Platform (multi-tenant)
├── Tenant A (Manufacturing)
│   ├── Project 1: SAP Finance Sidecar      [adapter: db(oracle) + msg(dingtalk)]
│   ├── Project 2: OA Approval Assistant     [adapter: api(seeyon) + built-in tools]
│   └── Project 3: General AI Assistant      [tools: search, browser, code]
├── Tenant B (Tech Company)
│   ├── Project 1: Salesforce CRM Agent      [adapter: api(salesforce) + msg(slack)]
│   └── Project 2: Internal Knowledge Agent  [tools: rag + built-in + MCP]
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

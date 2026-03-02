# Roadmap

> Goal: Build an **AI-powered Connector Hub** — Standalone (portal assistant), Copilot (embedded in host system), Hub (central cross-system orchestration).
>
> Principles: **Provider-agnostic** (no vendor lock-in), **minimal-abstraction**, **protocol-first**, **connector-first** (integration is the core value).

## Product Vision

FIM Agent is an **AI Connector Hub** that serves three progressive modes:

```
Standalone   → Your own AI assistant (Portal)
Copilot      → AI embedded in a host system (iframe / widget / embed)
Hub          → Central cross-system orchestration (Portal / API)
```

**Hub Mode is the core differentiator.** Enterprise clients have legacy systems — ERP, CRM, OA, finance, HR — that need to talk to each other through AI:

```
                        ┌───────────────────────────┐
                        │     FIM Agent Hub          │
                        │                            │
 ERP (SAP/Kingdee) ────│►  Agent A: Finance Audit   │───► Lark / Slack
 CRM (Salesforce)  ────│►  Agent B: Contract Review  │───► Email / WeCom
 OA (Seeyon/Weaver) ───│►  Agent C: Approval Assist  │───► Teams / Webhook
 Custom DB (PG/MySQL) ──│►  Agent D: Data Reporting   │───► Any API
                        │                            │
                        └──────────┬────────────────┘
                                   │
                   ┌───────────────┼───────────────┐
                   │               │               │
              Portal (UI)    API (headless)    iframe (embed)
```

**GTM path: Land and Expand**

| Step | Mode | What happens |
|------|------|-------------|
| Land | Copilot | Embed into one system, prove value inside their UI |
| Expand | Hub | Set up central portal, orchestrate across all systems |

## Delivery Modes (per-project)

| Mode | Description |
|------|-------------|
| **Portal (Web UI)** | Platform's built-in interface (standalone assistant) |
| **API (headless)** | Pure HTTP/SSE interface; no UI required; for embedding and programmatic access |
| **iframe / standalone URL** | Embed into host system pages; works with any mode |

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
> Multi-tenancy, persistence, agent management -- supports both standalone usage and the connector model that comes later.

- [x] **HTTP Request Tool**: General-purpose HTTP client (`http_request`) — supports GET/POST/PUT/PATCH/DELETE with custom headers, query params, and body; SSRF protection (private IP blocking), JSON auto-pretty-print, 200KB response limit
- [x] **Shell Exec Tool**: Sandboxed shell execution (`shell_exec`) — run curl, jq, awk, grep etc. with command blocklist (30+ patterns), env var scrubbing, system path write protection, per-user sandbox directory
- [x] **Persistent Storage**: SQLAlchemy ORM + SQLite (conversations, messages, model configs); async engine with WAL mode and StaticPool for concurrency
- [x] **Conversation Persistence**: Session history stored durably; multi-turn context via `DbMemory` — loads prior turns from DB, smart truncation with CJK-aware token estimation, auto-compact within token budget; both ReAct and DAG modes supported
- [x] **Multi-Tenant**: User registration/login (JWT), SSE token-based auth, conversation ownership validation
- [x] **Project & Agent Management**: Create/configure/publish agents; bind tools, model, and prompt per agent; agent-aware chat endpoints resolve LLM, tools, and instructions from agent config
- [x] **File Upload & Management**: Upload/download/delete files with per-user isolation; configurable upload directory
- [x] **Clipboard Image Paste**: Chat input supports pasting images directly from clipboard; same upload flow as file picker
- [x] **Drag-and-Drop File Upload**: Drag files onto the chat input area to upload; visual drop overlay with backdrop blur

### v0.5 -- RAG, Knowledge & Memory

> *"Give the Agent memory and knowledge"*

**Memory & Compact**
- [x] **LLM Compact**: When conversation history exceeds threshold, use a fast LLM to compress early turns into a summary; retain recent turns verbatim; transparent to the agent
- [x] **ContextGuard**: Unified context window budget manager -- checks message token counts against model limits (`LLM_CONTEXT_SIZE` / `LLM_MAX_OUTPUT_TOKENS`), applies hint-specific LLM compaction (react_iteration, planner_input, step_dependency), truncates oversized messages, falls back to smart truncation; wired into ReAct agent and DAG executor
- [x] **Pinned Messages**: `ChatMessage.pinned` flag protects critical messages (task description, user corrections) from compaction — ContextGuard and CompactUtils perform three-way split (system / pinned / compactable), pinned messages never enter the "old" pool for summarisation; ReAct pins the initial user message; DAG steps receive `original_goal` context so agents don't "forget what they're doing" during long tool-call loops; also fixes model_hint agents missing `context_guard` and `extra_instructions`

**DAG Mode Feature Parity** *(avoid falling behind ReAct)*
- [x] **DAG Multi-Turn Polish**: Currently injects history as text prefix to planner; upgrade to structured message history so planner can reason about prior tool results and plan evolution
- [x] **DAG LLM Compact**: Apply LLM compact to the enriched query before planning; long conversation context can blow up planner input
- [x] **DAG Re-Planning**: When the analyzer determines the goal was not achieved (confidence < 0.5), the pipeline automatically re-plans using previous step results as context and retries, up to 3 rounds; new SSE phase event `replanning`; done payload includes `rounds` count
- [x] **Tool-Name-Aware Planning**: Planner `tool_hint` constrained to available tool names; prevents hallucinated tool references in generated plans
- [x] **DAG Step-Level ReAct**: Each DAG step runs a full ReAct agent loop (multi-step reasoning + tool use) instead of a single LLM call; enables complex sub-tasks within a DAG node — the minimal viable unit of Multi-Agent capability, reusing existing DAG infrastructure
- [x] **DAG Step-Level Memory**: Each step executor sees relevant prior conversation context, not just the step task description
- [x] **DAG History Replay**: Frontend replays persisted DAG executions with the flow graph (currently only ReAct timeline replays correctly)

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

**Personal Center**
- [x] **Global User Instructions**: Per-user system instructions that inject into every agent conversation; PATCH `/api/auth/profile` to save; merged with agent-specific instructions (user instructions first, agent instructions after for higher LLM priority)

### v0.6 -- Connector Platform

> *"Connect any system through conversation"*
>
> The universal platform is ready. Now add connection capabilities so FIM Agent can bridge into any host system. The core differentiator.
>
> **Architecture**: Virtual Connector model — definitions stored in DB, runtime HTTP/SQL proxy (no MCP protocol overhead), export to standalone MCP Server for distribution/fork.

**Portal UX**
- [x] **Dark / Light / System Theme**: `next-themes` provider with system-preference detection, manual toggle (Appearance settings page), and full component theming across DAG visualization, playground, connectors, and KB pages; light-mode logo variant
- [x] **Suggested Follow-ups**: After agent completes (ReAct/DAG), fast LLM generates 2-3 follow-up questions displayed as clickable chips below the answer; click to auto-submit as new query; ephemeral — not persisted to DB, disappears on page refresh

#### v0.6.1 — Connector Entity & Manual Builder (shipped)

**Backend**
- [x] Connector + ConnectorAction ORM models
- [x] Connector CRUD API: `/api/connectors`
- [x] ConnectorAction CRUD API: `/api/connectors/{id}/actions`
- [x] `ConnectorToolAdapter` (like MCPToolAdapter): Action → BaseTool; `run()` builds HTTP request → `base_url + path` → returns response; auth injection (bearer with configurable prefix, API key with custom header, basic auth) with default credential fallback from `auth_config`
- [x] Agent model `connector_ids: JSON` field
- [x] `_resolve_tools()` extension: load agent-bound connectors → register actions as tools
- [x] Database migration

**Frontend**
- [x] Connector management page (list + create/edit form)
- [x] Action editor (method, path, parameters schema)
- [x] Agent form Connector multi-select (like KB binding)
- [x] Tool category `"connector"` in sidebar
- [x] Per-auth-type config UI (bearer token prefix + default token, API key header name + default key, basic auth username/password)

**Validation**: GitHub Connector (PAT bearer auth) → list_repos / get_issue → bind to Agent → use in conversation.

#### v0.6.2 — Per-User Credentials

**Backend**
- [ ] ConnectorCredential model (AES-GCM encrypted)
- [ ] Credential CRUD API: `/api/connectors/{id}/credentials`
- [ ] Runtime credential injection: `ConnectorToolAdapter.run()` loads current user credential → injects HTTP header
- [ ] Target system error passthrough: 4xx/5xx returned to Agent as-is
- [ ] Auth type support: api_key (custom header), bearer, basic, none
- [ ] Token exchange: app_id + secret → access_token (for services like Feishu/Lark that need two-step auth)

**Frontend**
- [ ] Connector detail page + "Connect" button
- [ ] Credential input dialog (dynamic fields based on auth_type)
- [ ] Connection status indicator (connected / not connected / expired)

**Validation**:
1. GitHub: two users with separate PATs → same Connector → each sees own repos → 403 passthrough on no permission
2. Feishu Connector: docs/messages/calendar APIs → user connects via Feishu app credentials → chat queries own Feishu docs and messages
3. Confirmation Gate: Agent attempts POST to GitHub → user sees confirmation dialog → approve/reject → result returned

**Safety**
- [ ] **Confirmation Gate**: Write operations (POST/PUT/DELETE) require user confirmation before execution; SSE event `confirmation_required`; configurable per-action (some reads may also need confirmation for sensitive data)
- [ ] **Dry-run Mode**: Preview operation effects without actual execution

#### v0.6.3 — AI-Assisted Connector Builder

**Backend**
- [ ] OpenAPI Spec import: upload/URL → parse → auto-generate Actions
- [ ] Connector Builder Agent (built-in) with dedicated tools: `create_connector_draft` / `add_action` / `test_action` / `import_openapi` / `publish_connector`
- [ ] API probing: test endpoints with provided credentials
- [ ] Natural language → Action conversion

**Frontend**
- [ ] Builder conversation entry (reuse Chat UI + built-in Builder Agent)
- [ ] OpenAPI import entry

**Validation**: Create Zhihe contract system Connector via conversation — provide API docs → Agent generates actions → test → publish.

**Example scenarios Connectors unlock:**
- **ERP / Finance** (SAP, Kingdee/金蝶, Oracle DB): Agent reads financial statements via DB Connector, generates analysis reports, pushes to Lark (飞书) / Slack
- **OA / Workflow** (Seeyon/致远, Weaver/泛微, REST API): Agent calls approval endpoints via API Connector, AI-assisted classification and routing, writes results back
- **CRM / Contracts** (Salesforce, custom PG): Agent reads contract clauses via API Connector, performs risk analysis, generates review opinions
- **Business DB** (MySQL / PG): Agent scans for anomalies via DB Connector, generates alerts, notifies responsible parties via Teams / WeCom (企微) / email

### v0.7 -- Connector Distribution & OAuth

> *"Build once, distribute to many"*

- [ ] **Connector Export/Import**: Export JSON/YAML (without credentials), others can import
- [ ] **Fork Mechanism**: Create a copy from a published Connector, freely modify actions
- [ ] **OAuth 2.0 Support**:
  - OAuth configuration (client_id, client_secret, auth_url, token_url, scopes)
  - User clicks "Connect" → OAuth authorization page → callback → store refresh_token
  - Automatic token refresh (silent refresh before expiry)
- [ ] **Connector Versioning**: Track changes, support rollback
- [ ] **Official Connector Library**: GitHub, Lark, Slack and other preset connectors
- [ ] **MCP Server Export**: Generate standalone FastMCP Server code from Connector; users can run independently or fork

### v0.8 -- Database Connector, Message Push & Access Control

> *"Not just APIs — databases, notifications, and who can do what"*

**Connector Types**
- [ ] **Database Connector Type**:
  - Support PostgreSQL, MySQL (asyncpg / aiomysql)
  - Schema introspection: AI browses table structure, suggests useful queries
  - Action = parameterized SQL query (read-only by default, writes require confirmation)
  - Connection pool management, query timeout protection
- [ ] **Message Push Connector Type**:
  - Send results to Lark / WeCom / Slack / email / webhook
  - Templated message formatting
  - Can serve as Agent's "output channel"

**Access Control & Governance**
- [ ] **RBAC (Role-Based Access Control)**: Roles (admin / editor / viewer) with granular permissions on agents, connectors, and knowledge bases; platform admin can manage users and roles; replaces current flat JWT auth with hierarchical access model
- [ ] **Operation Audit Log**: Every tool call recorded (timestamp, user, Connector, Action, params, result); filterable audit trail UI in admin panel

**Reference Implementation**
- [ ] **Zhihe Contract System Reference Connector**: First official business connector — search / detail / compare / timeline / statistics

**Planning & Execution Intelligence**
- [ ] **DAG Execution Gating (HITL)**: Human-in-the-loop checkpoints during DAG execution — Plan Preview (show generated plan → user approve/modify/reject before execution), Step-level Gates (`GateNode` type with `single_select` / `multi_select` / `freeform` interaction modes), Confidence-based Soft Gates (planner confidence < threshold triggers user review, timeout auto-selects default); SSE event `gate_required`; complements v0.6.2 Confirmation Gate (connector write ops) with plan-level oversight
- [ ] **KB Routing Strategy**: Multi-layer knowledge base selection — L1: static `kb_scope` per step (whitelist within agent's `kb_ids`), L2: priority hints in step config (`high` / `medium` / `low` with trigger conditions), L3: optional agent dynamic selection within whitelist for large KB pools (>5); reduces unnecessary retrieval, improves grounding precision

### v0.9 -- Sandbox Hardening + Observability

> *"Production-ready"*

**Sandbox Hardening**
- [ ] **Per-User Virtual Environment**: Each conversation spawns an isolated Python venv; user code executes via subprocess in the venv, not the host interpreter; prevents cross-session state leakage and module pollution
- [ ] **Resource Limits**: CPU time and memory caps via `resource.setrlimit()` (Unix) for both Python exec and shell exec; prevents OOM and infinite-loop DoS
- [ ] **File I/O Sandboxing**: Replace whitelisted `open()` in python_exec with path-validated wrapper; all file access confined to conversation sandbox directory
- [ ] **Shell Exec Allowlist Mode**: Optional switch from command blocklist (regex-bypassable) to strict allowlist (`curl`, `grep`, `jq`, `awk`, `sed`, etc.); defense-in-depth against variable expansion / command substitution bypass

**Observability**
- [ ] **OpenTelemetry Tracing**: Full-chain tracing for LLM calls, tool execution, and Connector invocations
- [ ] **Cost Dashboard**: Token usage aggregation by user / project / agent / model
- [ ] **Connector Analytics**: Per-connector call volume, error rate, p95 latency, uptime tracking; surface unhealthy connectors and usage trends
- [ ] **Circuit Breaker**: Connector connection circuit breaker with graceful degradation
- [ ] **Health Check**: `/api/health` endpoint (LLM + Connector + vector store connectivity)
- [ ] **Execution Replay**: Complete execution trace replay for debugging and auditing
- [ ] **Docker Compose**: API + SQLite + optional Langfuse, production-ready

**i18n**
- [ ] **Chinese / English**: Frontend + backend error messages; locale auto-detection + manual switch

### v1.0 -- Enterprise & Scale

> *"Enterprise-ready"*

**Connector Ecosystem**
- [ ] **AI Connector Generation**: Upload Swagger/OpenAPI spec → AI auto-generates complete Connector (auth, Actions, tests)
- [ ] **Connector Marketplace**: In-platform connector market — search, install, rate
- [ ] **Plugin System**: Pip-installable Connector packages with entry-point auto-registration

**Enterprise Operations**
- [ ] **Admin Dashboard**: Audit logs, Connector management, health monitoring, usage/cost analytics
- [ ] **Scheduled Jobs**: Cron triggers, webhook triggers — run agents on schedule or in response to external events
- [ ] **Batch Execution**: Run an agent against multiple inputs in one job (e.g., review 100 contracts, audit 50 invoices); progress tracking, partial failure handling, result aggregation
- [ ] **Enterprise Security**: Data encryption, IP whitelisting, SOC2 audit logging
- [ ] **PostgreSQL**: Optional for large-scale deployments

**Embeddable Delivery**
- [ ] **Embeddable Widget**: Lightweight JS bundle (<100KB), inject via `<script>` tag into host pages
- [ ] **Page Context Injection**: Read current context from host page (contract ID, page URL, DOM selector)
- [ ] **iframe / Standalone URL Mode**: Authenticated standalone access for embedding

---

## Connector Standardization Levels

| Level | Version | Approach |
|-------|---------|----------|
| **Level 1** | v0.6 | Manual/AI-created Connector, DB storage, runtime HTTP proxy |
| **Level 2** | v0.7 | Export/Import + Fork, MCP Server export |
| **Level 3** | v1.0 | Upload OpenAPI spec, AI auto-generates complete Connector |

## Multi-Tenant Model

```
Platform (multi-tenant)
├── Tenant A (Manufacturing)
│   ├── Project 1: SAP Finance Copilot      [connector: db(oracle) + msg(lark)]
│   ├── Project 2: OA Approval Assistant     [connector: api(seeyon) + built-in tools]
│   └── Project 3: General AI Assistant      [tools: search, browser, code]
├── Tenant B (Tech Company)
│   ├── Project 1: Salesforce CRM Agent      [connector: api(salesforce) + msg(slack)]
│   └── Project 2: Internal Knowledge Agent  [tools: rag + built-in + MCP]
```

### Consider

> Items worth exploring but deferred from version roadmap. Priority labels indicate likelihood of future promotion.

**P2 — Worth building if the product direction supports it**

- [ ] **Static Pipeline Template (L1 + L2 Architecture)**: User-defined DAG templates with fixed node structure and edges; each node constrains execution scope (allowed tool set, KB whitelist, token budget) while the agent dynamically decides details within constraints. L1 static layer = user-defined flow (JSON template, no visual editor initially), L2 dynamic layer = existing DAGPlanner inside each node. *Deferred because: requires significant frontend investment (workflow builder UI) and the industry hasn't converged on whether visual workflow builders or prompt-driven planning is the better abstraction for agent orchestration. Re-evaluate when connector scenarios demand fixed multi-step pipelines that pure LLM planning cannot reliably produce.*

**P3 — Deferred indefinitely; being absorbed by LLM providers or already solved**

- [ ] **Conversation Summary Memory**: Automatic rolling summaries that persist across long sessions; hybrid window + summary strategy. *Reason: Largely overlaps with LLM Compact (shipped in v0.5). The compact mechanism already summarizes old turns via fast LLM; a separate summary memory adds marginal value.*
- [ ] **Multi-Agent Orchestration**: Leader/Worker agent pool, task graph with dependency edges, inter-agent messaging, graceful shutdown, token economics tracking. *Reason: LLM providers are building this natively — OpenAI Swarm, Anthropic Claude Code Teams, Google A2A protocol. The orchestration layer is commoditizing at the provider level; competing with first-party implementations is not a sustainable differentiator.*
- [ ] **Semantic Memory Store**: Cross-conversation knowledge extraction and retrieval; agent remembers facts/preferences across sessions via embedding-based lookup. *Reason: Model context windows are growing rapidly (Gemini 2M+, Claude 200K+), reducing the need for external memory management. Meanwhile, providers are adding native memory/personalization features (ChatGPT Memory, Claude Projects). The gap that semantic memory fills is shrinking with each model generation.*
- [ ] **Memory Lifecycle**: TTL-based expiry, importance scoring, explicit forget/remember commands. *Reason: Same as Semantic Memory Store — as models gain longer context and native memory capabilities, building a custom memory lifecycle system risks becoming redundant engineering effort.*

---

Contributions and ideas are welcome -- open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

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
- [x] **ContextGuard**: Unified context window budget manager — automatically compacts or truncates messages to stay within model token limits; wired into both ReAct and DAG execution
- [x] **Pinned Messages**: Critical messages (task description, user corrections) protected from compaction; pinned messages never summarized during LLM Compact

**DAG Mode Feature Parity** *(avoid falling behind ReAct)*
- [x] **DAG Multi-Turn & Compact**: Structured message history for planner (replaces text prefix) with LLM compaction to keep planner input within token budget
- [x] **DAG Re-Planning**: When the analyzer determines the goal was not achieved (confidence < 0.5), the pipeline automatically re-plans using previous step results as context and retries, up to 3 rounds
- [x] **DAG Step-Level ReAct**: Each DAG step runs a full ReAct agent loop (multi-step reasoning + tool use) instead of a single LLM call; enables complex sub-tasks within a DAG node
- [x] **DAG History Replay**: Frontend replays persisted DAG executions with the flow graph

**RAG & Knowledge Base**
- [x] **Embedding**: `BaseEmbedding` protocol + OpenAI-compatible implementation (Jina jina-embeddings-v3)
- [x] **Vector Store**: LanceDB embedded vector store -- zero external services, native FTS, content-hash dedup
- [x] **Document Loaders**: PDF, DOCX, Markdown, HTML, CSV, plain text
- [x] **Chunking Strategies**: Fixed-size, recursive, and semantic chunking
- [x] **Hybrid Retrieval**: Dense vector search + LanceDB native FTS + RRF fusion + Jina reranker
- [x] **Knowledge Base Management**: Full KB CRUD with document upload, background ingest, and agent `kb_retrieve` tool
- [x] **Grounded Generation**: Evidence-anchored RAG — auto-upgrades to grounded retrieval with citation extraction, alignment scoring, conflict detection, and confidence computation; inline citation markers with structured References panel
- [x] **Chunk-Level CRUD**: View, edit, and delete individual chunks within a knowledge base document; inline chunk editor with content preview
- [x] **KB Detail Page**: Dedicated knowledge base detail page with document table and chunk browser; navigate from KB list to full document/chunk management
- [x] **Markdown Document Creation**: Create markdown documents directly from the KB UI; content authored in-browser and ingested into the knowledge base

**Conversation Management**
- [x] **Conversation Starring & Pinning**: Star/pin important conversations for quick access; starred conversations surface to the top of the sidebar
- [x] **Batch Delete Conversations**: Select and delete multiple conversations at once from the sidebar
- [x] **Chat Search (Cmd+K)**: Command palette–style search dialog for quickly finding conversations by title or content
- [x] **Conversation Rename**: Inline rename conversations directly from the sidebar

**Personal Center**
- [x] **Global User Instructions**: Per-user system instructions that inject into every agent conversation; merged with agent-specific instructions

### v0.6 -- Connector Platform

> *"Connect any system through conversation"*
>
> The universal platform is ready. Now add connection capabilities so FIM Agent can bridge into any host system. The core differentiator.
>
> **Architecture**: Virtual Connector model — definitions stored in DB, runtime HTTP/SQL proxy (no MCP protocol overhead), export to standalone MCP Server for distribution/fork.

**Portal UX**
- [x] **Dark / Light / System Theme**: `next-themes` provider with system-preference detection, manual toggle (Appearance settings page), and full component theming
- [x] **Suggested Follow-ups**: Fast LLM generates 2-3 follow-up questions as clickable chips after agent completes
- [x] **Conversational Interrupt**: Send messages while agent is running; inject mode with Stop button; messages persist for replay
- [x] **DAG Interrupt Skip**: Skip running/pending DAG steps during conversational interrupt
- [x] **MarkItDown Integration**: DOCX/XLSX/PPTX document loading via MarkItDown library
- [x] **Env-Driven Execution Limits**: Configurable `MAX_REACT_ITERATIONS` and `MAX_DAG_STEPS` via environment variables

#### v0.6.1 — Connector Entity & Manual Builder (shipped)

- [x] **Connector CRUD**: Create, edit, delete connectors with actions; full REST API (`/api/connectors`)
- [x] **Connector Tool Adapter**: Actions registered as Agent tools; HTTP proxy with auth injection (bearer, API key, basic auth) and default credential fallback
- [x] **Agent Connector Binding**: Bind connectors to agents (like KB binding); agent resolves connector actions as tools at runtime
- [x] **Connector Management UI**: Connector list, create/edit form, action editor (method, path, parameters), per-auth-type config
- [x] **Agent Form Connector Selector**: Multi-select connector binding in agent configuration form
- [x] **Connector Smart Truncation**: JSON-aware response truncation with env-configurable array item limit and character limit
- [x] **KB Document Retry**: Failed document ingestion can be retried; frontend retry button with error tooltip
- [x] **Chunk Search & Filter**: Text search/filter for chunks within the KB chunk drawer
- [x] **Observation JSON Highlighting**: Auto-detect and syntax-highlight JSON in tool output

**Validation**: GitHub Connector (PAT bearer auth) → list_repos / get_issue → bind to Agent → use in conversation.

#### v0.6.2 — AI-Assisted Connector Builder (shipped)

**Backend**
- [x] OpenAPI Spec import: paste/URL → parse → auto-generate Connector + Actions (one-shot and add-to-existing modes)
- [x] Natural language → Action conversion: AI generate-actions and refine-action endpoints powered by fast LLM
- [ ] Connector Builder Agent (built-in) with dedicated tools: `create_connector_draft` / `add_action` / `test_action` / `import_openapi` / `publish_connector`
- [ ] API probing: test endpoints with provided credentials

**Frontend**
- [x] OpenAPI import dialog (paste YAML/JSON or URL, reusable for both create and add-actions modes)
- [x] AI Action Panel: inline collapsible chat panel in action editor (generate → refine flow)
- [ ] Builder conversation entry (reuse Chat UI + built-in Builder Agent)
- [x] Conversational Connector Settings: chat-based form for creating and editing connectors and actions through dialogue
- [x] User Language Preference: `preferred_language` setting (auto/en/zh) with language directive injection across all LLM interactions; language selector in user dropdown
- [ ] Conversational Agent Settings: port the conversational settings pattern to agent create/edit (same chat-based CRUD approach)

**Validation**: Create Zhihe contract system Connector via conversation — provide API docs → Agent generates actions → test → publish.

**Example scenarios Connectors unlock:**
- **ERP / Finance** (SAP, Kingdee/金蝶, Oracle DB): Agent reads financial statements via DB Connector, generates analysis reports, pushes to Lark (飞书) / Slack
- **OA / Workflow** (Seeyon/致远, Weaver/泛微, REST API): Agent calls approval endpoints via API Connector, AI-assisted classification and routing, writes results back
- **CRM / Contracts** (Salesforce, custom PG): Agent reads contract clauses via API Connector, performs risk analysis, generates review opinions
- **Business DB** (MySQL / PG): Agent scans for anomalies via DB Connector, generates alerts, notifies responsible parties via Teams / WeCom (企微) / email

### v0.7 -- SaaS Runtime & Provider Abstraction

> *"Secure execution and pluggable services for multi-tenant deployment"*
>
> SaaS deployment means untrusted code from multiple users runs on shared infrastructure. The in-process python_exec sandbox (module blocklist + timeout) is a critical security gap — container-level or microVM isolation is required. Meanwhile, hardcoded service providers (Jina Search/Fetch) must become pluggable for global availability and provider flexibility.

**Sandbox Execution Backend**
- [ ] **Code Execution Sandbox**: Replace in-process `exec()` with container/microVM isolation for SaaS deployment; each execution runs in an isolated environment with its own filesystem and network stack; configurable backend via `CODE_EXEC_BACKEND` env var (`local` for self-hosted backward compatibility, `e2b` for SaaS via E2B Python SDK); `local` mode retains current in-process sandbox for zero-dependency self-hosted deployments
- [ ] **Sandbox Lifecycle Management**: Sandbox pooling and reuse within a conversation session; idle timeout and cleanup; resource limits (CPU, memory, execution time) configurable per agent
- [ ] **Shell Exec Sandboxing**: Route `shell_exec` tool through the same sandbox backend; SaaS mode executes shell commands inside sandbox, not on host
- [ ] **Multi-Language Execution**: Sandbox kernels support Python, JavaScript, TypeScript, Bash; expose language selection in code execution tool (extend beyond Python-only)

**Service Provider Abstraction**
- [ ] **Web Search Adapter**: `BaseWebSearch` protocol with pluggable implementations — Jina Search (default, free tier), Tavily, Google Custom Search, Brave Search; configured via `SEARCH_PROVIDER` + provider-specific env vars; graceful fallback chain
- [ ] **Web Fetch Adapter**: `BaseWebFetch` protocol — Jina Reader (default), self-hosted httpx + html2text fallback; configured via `FETCH_PROVIDER` env var
- [ ] **Reranker Providers**: Add Cohere Reranker and OpenAI-compatible reranker implementations alongside existing Jina; configured via `RERANKER_PROVIDER` env var; `BaseReranker` interface already exists, only new implementations needed

### v0.8 -- Organization, Connector Distribution & OAuth

> *"Who uses what — then distribute everywhere"*
>
> Organization is the foundation for enterprise deployment. Admin configures connectors, agents, and knowledge bases, then publishes to org members. Per-user credentials let each member authenticate to shared connectors with their own identity. Combined with connector distribution and OAuth, this version makes FIM Agent deployable to enterprise teams — like OpenAI / Anthropic Console's org model.

**Organization & Resource Sharing**
- [ ] **Organization Model**: Org CRUD (create, rename, delete); member invite (email or invite link) and remove; two roles — **admin** (full configuration: create/edit/publish connectors, agents, KBs, manage members) and **member** (use published resources, manage own conversations and credentials). Replaces current flat JWT auth with org-scoped access.
- [ ] **Resource Visibility (3-tier)**: Connector / Agent / KB gain `org_id` + `visibility` field with three levels:
  - `personal` — only creator can see/use (default)
  - `org-shared` — all org members can use (admin publishes; read-only for members)
  - `public` — **Agent and Connector only, KB excluded**. Agent public = template marketplace (users clone and bind their own KB/Connector credentials; cannot use another user's agent instance directly). Connector public = cross-org distribution (API definitions contain no user data, natural fit for sharing). KB contains enterprise documents — global sharing is a security risk, org-shared is sufficient.
- [ ] **KB Tags**: Tag-based categorization for knowledge bases; org members can filter/discover KBs by tag (e.g., "contract", "finance", "HR", "regulations"); tags managed by admin, displayed as filterable chips in KB list
- [ ] **Frontend — Organization Management**: Org settings page (name, logo, invite link), member list with role badges, invite flow, resource publish/unpublish toggle on Connector/Agent/KB detail pages; org switcher in top nav (for users in multiple orgs)

**Connector Distribution**
- [ ] **Connector Export/Import**: Export JSON/YAML (without credentials), others can import
- [ ] **Fork Mechanism**: Create a copy from a published Connector, freely modify actions
- [ ] **Connector Versioning**: Track changes, support rollback
- [ ] **Official Connector Library**: GitHub, Lark, Slack and other preset connectors
- [ ] **MCP Server Export**: Generate standalone FastMCP Server code from Connector; users can run independently or fork

**Per-User Credentials**

> *Connectors are now org-shared — each member needs their own credentials to access the target system.*

- [ ] **ConnectorCredential Model**: Per-user credential storage (AES-GCM encrypted at rest); unique constraint `(user_id, connector_id)`; status tracking (connected / expired / error)
- [ ] **Credential CRUD API**: `PUT /api/connectors/{id}/credentials` (upsert, encrypt), `GET` (status + masked preview, no plaintext), `DELETE` (disconnect), `POST .../credentials/test` (connectivity check)
- [ ] **Runtime Credential Injection**: `_get_tools()` loads current user's credential → decrypts → passes to `ConnectorToolAdapter(auth_credentials=...)`; falls back to connector default `auth_config` if no user credential
- [ ] **Target System Error Passthrough**: 4xx/5xx from target API returned to Agent as-is (status code + error body)
- [ ] **Token Exchange**: Support `oauth_app` auth type — store `app_id + app_secret + token_url`; runtime POST to exchange for access_token; cache until expiry (e.g., Feishu/Lark `tenant_access_token`)
- [ ] **Connector Detail Page**: `/connectors/[id]` — connector info, action list, **"Connect" button** to bind user credentials
- [ ] **Credential Input Dialog**: Dynamic form fields based on `auth_type` (bearer → token, api_key → key + header, basic → username + password, oauth_app → app_id + secret)
- [ ] **Connection Status Indicator**: Badge on ConnectorCard and detail page — `connected` (green) / `not connected` (gray) / `expired` (yellow)

**Validation**:
1. GitHub: two users with separate PATs → same org-shared Connector → each sees own repos → 403 passthrough on no permission
2. Feishu: docs/messages/calendar APIs → user connects via Feishu app credentials → chat queries own Feishu data

**OAuth 2.0**
- [ ] **OAuth 2.0 Support**:
  - OAuth configuration (client_id, client_secret, auth_url, token_url, scopes)
  - User clicks "Connect" → OAuth authorization page → callback → store refresh_token
  - Automatic token refresh (silent refresh before expiry)
  - Works with Organization: admin configures OAuth app credentials at org level, each member authorizes individually

### v0.9 -- Database Connector, Message Push & Governance

> *"Not just APIs — databases, notifications, and operational safety"*

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

**Governance**
- [ ] **Operation Audit Log**: Every tool call recorded (timestamp, user, Connector, Action, params, result); filterable audit trail UI in admin panel
- [ ] **Confirmation Gate**: User confirmation before connector write operations and cross-connector actions. Especially critical in Hub mode where connectors communicate through the platform (e.g., Agent reads from CRM → writes to ERP → sends notification via Lark — each write hop should be user-confirmed). Four-option permission model inspired by Claude Code:
  - **Allow once**: approve this specific operation
  - **Allow always for this action**: auto-approve future calls to the same connector action (persisted per user)
  - **Deny once**: reject this specific operation
  - **Deny always for this action**: auto-reject future calls to the same connector action
  - SSE event `confirmation_required` with operation details (connector, action, method, URL, params preview)
  - Configurable per action: `requires_confirmation: true/false` (default true for POST/PUT/DELETE, false for GET)
  - Dry-run mode (optional): preview operation effects without actual execution

**Reference Implementation**
- [ ] **Zhihe Contract System Reference Connector**: First official business connector — search / detail / compare / timeline / statistics

**Planning & Execution Intelligence**

- [ ] **Plan Preview (DAG only)**:
  After DAGPlanner generates the full execution plan, present it to the user as an editable step list **before any execution begins**.
  User can: approve as-is, reorder steps, add/remove steps, modify tool/KB bindings per step, inject constraints (e.g., "skip step 2", "step 3 must use the contract regulations KB").
  Primary controllability mechanism — user sees the entire plan before anything runs.
  Configurable per agent: `plan_review: true/false` (default false for backward compatibility).

- [ ] **Soft Gate (DAG + ReAct)**:
  Intelligent, zero-config gating — the agent **autonomously pauses when uncertain** and asks the user for guidance. No manual gate schema or per-step configuration needed; the only setting is a global confidence threshold.

  **How it works:**
  - During execution, whenever the agent faces an ambiguous decision (which tool to use, which KB to query, which approach to take), it evaluates confidence across candidate options
  - If max confidence < threshold (default 0.5, configurable per agent), the agent pauses and presents candidates as a single-select gate via SSE event `gate_required`
  - Frontend renders options with confidence percentages and a countdown timer
  - If user selects an option → inject into context and continue
  - If user doesn't respond within timeout (default 30s, configurable) → auto-select the highest-confidence option and continue
  - Works in both DAG (planner-level and step-level uncertainty) and ReAct (tool selection and parameter uncertainty)

  **Example scenarios:**
  - ReAct: Agent bound to 3 KBs, unsure which to query → presents KB options with relevance scores
  - ReAct: Agent unsure whether to use `web_search` or `kb_retrieve` → presents tool options
  - DAG: Planner unsure whether to split into 2 or 3 steps → presents plan alternatives
  - DAG step: Step-level ReAct agent encounters same tool/KB ambiguity as standalone ReAct

  **Relationship to other gates (all in v0.9):**
  - Confirmation Gate: **hardcoded safety** — connector write operations require user permission (allow once / allow always / deny)
  - Plan Preview: **explicit user review** — user opts in to review every DAG plan before execution
  - Soft Gate: **intelligent safety net** — agent self-triggers only when uncertain, zero config overhead

  **Implementation:**
  - SSE event `gate_required` with `{type: "soft_gate", options: [...], confidence: [...], timeout: 30}`
  - User response via POST `/api/chat/{id}/gate_response`
  - Agent config: `soft_gate_threshold: float` (default 0.5), `soft_gate_timeout: int` (default 30s)
  - No changes to DAG schema or tool bindings — Soft Gate is an execution-time behavior, not a configuration-time concept

### v1.0 -- Blueprint Mode & Production Hardening

> *"A familiar visual shell for enterprise clients, then harden for production"*
>
> Enterprise clients accustomed to Dify-style visual workflows need a controllable, auditable execution model before adopting AI agents. Blueprint Mode provides the static pipeline shell — drag-and-drop nodes, per-node KB/tool/prompt binding, IF/ELSE branching — while the agent handles dynamic reasoning within each node's scope. Combined with observability and production hardening, this version makes FIM Agent enterprise-deployable.

**Blueprint Mode (Static Pipeline + Dynamic Execution)**

- [ ] **Visual Pipeline Builder**: Lightweight drag-and-drop editor for defining static execution pipelines — a Dify-equivalent visual workflow that enterprise clients can adopt without fear of uncontrollable AI behavior:
  - Canvas with draggable nodes and connectable edges
  - Node types: Action (tool/agent execution), Gate (guided HITL dialog from v0.8), Condition (if-else branching), Start/End
  - Per-node configuration: tool set binding, KB scope (which knowledge bases this node can access), system prompt / instruction template, model override, token budget
  - Conditional branches: rule-based (field value matching) or LLM-evaluated (confidence-based routing)
  - Pipeline saved as versioned JSON template; one template can be bound to multiple agents
  - **Agent is the publishing entity** — Blueprint defines *how* the agent works; Agent defines *what* external users see. Publishing (v1.1) exposes the Agent; the Blueprint is an internal execution detail invisible to end users

- [ ] **Execution Model — Three Progressive Phases**:

  **Phase 1 — Fully Static (Dify-equivalent, ship first)**:
  Pure static planning + pure static execution. No agent reasoning inside nodes — each node runs a **fixed prompt template** against a designated LLM, with deterministic input/output wiring between nodes. This is the baseline for enterprise clients migrating from Dify:
  - Each node has a fixed prompt template with `{{variable}}` placeholders filled from upstream node outputs
  - KB retrieval nodes query explicitly bound knowledge bases with fixed retrieval parameters (top_k, threshold) — no agent decision-making
  - Tool call nodes invoke a single pre-configured tool with static parameters — no tool selection
  - Conditional branches evaluate deterministic rules (field value matching, threshold comparison)
  - Execution is fully reproducible: same input → same node sequence → same output (modulo LLM non-determinism)
  - No Soft Gate needed (nothing is uncertain — everything is pre-configured)
  - Essentially a Dify workflow runtime with FIM Agent's connector and KB infrastructure

  **Phase 2 — Semi-dynamic (agent-assisted nodes)**:
  Selected nodes upgrade from fixed prompt templates to agent-mode execution — the node scope (tool set, KB whitelist, token budget) is still statically defined, but within those boundaries the agent can reason, select tools, and iterate. Non-critical nodes remain fully static. Soft Gate (v0.8) activates for agent-mode nodes to handle uncertainty.

  **Phase 3 — Fully dynamic (no blueprint)**:
  No static pipeline — pure DAGPlanner generates and executes the plan autonomously. This is the current default behavior, suitable for advanced users comfortable with AI-driven execution.

- [ ] **Template Library**: Pre-built pipeline templates for common enterprise scenarios:
  - Contract Review: document parsing → clause extraction → risk analysis → review report
  - Finance Reconciliation: data collection → variance comparison → anomaly flagging → report generation
  - Approval Assist: application parsing → rule matching → recommendation generation → notification push
  - Templates are forkable — clients can copy and customize

**Observability**
- [ ] **OpenTelemetry Tracing**: Full-chain tracing for LLM calls, tool execution, and Connector invocations
- [ ] **Cost Dashboard**: Token usage aggregation by user / project / agent / model
- [ ] **Connector Analytics**: Per-connector call volume, error rate, p95 latency, uptime tracking; surface unhealthy connectors and usage trends
- [ ] **Circuit Breaker**: Connector connection circuit breaker with graceful degradation
- [ ] **Health Check**: `/api/health` endpoint (LLM + Connector + vector store connectivity)
- [ ] **Execution Replay**: Complete execution trace replay for debugging and auditing
- [ ] **Docker Compose**: API + SQLite + optional Langfuse, production-ready

**i18n**
- [x] **User Language Preference**: `preferred_language` backend setting with language directive injection across all LLM interactions
- [ ] **Frontend i18n**: `next-intl` integration with en/zh translation files; locale driven by `preferred_language` (auto mode falls back to `navigator.language`); all UI text externalized

### v1.1 -- Agent as a Service

> *"Expose your agents to the world"*
>
> Everything before this version builds the internal platform — agents, connectors, knowledge bases, governance, observability. This version turns internal agents into externally accessible services. Like Dify's app publishing: create an agent → publish → anyone can use it via Web App, API, or embedded widget. Combined with enterprise operations, this makes FIM Agent both a builder platform and a distribution platform.

**Agent Publishing**

> *The core capability: turn any agent into an externally accessible service with one click.*

- [ ] **Published Agent Page**: Publish an agent → generates a standalone URL (`/app/{slug}`) with branded chat interface (agent icon, name, description, welcome message, suggested questions); visitors interact without needing a platform account; mobile-responsive
- [ ] **Agent API Mode**: Per-published-agent REST + SSE endpoints (`/api/apps/{slug}/chat`); independent from internal `/api/chat` — external consumers call with API key, no platform account needed; OpenAI-compatible response format option for easy integration
- [ ] **API Key Management**: Generate/revoke API keys per published agent; each key has its own rate limit (RPM / RPD / monthly token quota); usage tracked per key; key-level analytics
- [ ] **Publish Controls**: Publish/unpublish toggle; draft preview before going live; version snapshot — publishing locks the current agent config (model, tools, connectors, KB, prompt, **and Blueprint version if bound**), further edits don't affect the live version until re-published; supports all three execution modes behind the same published interface (Blueprint static pipeline / DAG dynamic planning / ReAct single-query) — external users see only a chat box, the execution strategy is transparent
- [ ] **Custom Branding**: Per-agent landing page customization — logo, color accent, welcome message, example conversations, footer links; white-label option for enterprise tier

**Delivery Channels (3 modes)**

> *Same agent, three ways to reach users — just like Dify.*

- [ ] **Web App Mode**: Full-featured standalone chat page at `/app/{slug}` — streaming responses, tool step display, KB citations, file upload; shareable link; optional password protection
- [ ] **API Mode (headless)**: REST + SSE for programmatic access — `POST /api/apps/{slug}/conversations` to start, SSE stream for responses; supports both streaming and blocking modes; webhook callback for async completion notification; SDKs (Python, JS/TS)
- [ ] **Embed Mode**: Lightweight JS widget bundle (<100KB gzip) injected via `<script>` tag; configurable position (bottom-right bubble, full sidebar, inline); theme matching (inherit host page colors or use agent branding); iframe fallback for strict CSP environments; page context injection — read current URL, DOM selectors, or custom variables passed from host page

**Access Control & Security**

- [ ] **Visitor Modes**: Three access levels per published agent — `open` (anonymous, anyone with the link), `link-only` (shareable link with optional expiry and usage cap), `authenticated` (require login or API key)
- [ ] **Rate Limiting**: Per-agent rate limiting — requests per minute, requests per day, monthly token budget; separate limits for Web App visitors vs API consumers; graceful 429 responses with retry-after
- [ ] **Domain Restrictions**: Whitelist allowed origins for Embed mode; prevent unauthorized sites from embedding your agent
- [ ] **IP Allowlist/Blocklist**: Optional per-agent IP filtering for API mode; useful for restricting access to known client IPs

**Usage Analytics**

- [ ] **Per-Agent Dashboard**: Call volume, token consumption (input/output), unique visitors, average response time, error rate; time-series charts (daily/weekly/monthly); breakdown by delivery channel (Web App / API / Embed)
- [ ] **Conversation Logs**: Browse and search conversations from published agents; filter by time, visitor, satisfaction; export CSV for analysis
- [ ] **Cost Attribution**: Token cost breakdown by agent, by API key, by time period; alert when approaching budget limits

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

---

## Connector Standardization Levels

| Level | Version | Approach |
|-------|---------|----------|
| **Level 1** | v0.6 | Manual/AI-created Connector, DB storage, runtime HTTP proxy |
| **Level 2** | v0.8 | Export/Import + Fork, MCP Server export |
| **Level 3** | v1.1 | Upload OpenAPI spec, AI auto-generates complete Connector |

## Multi-Tenant Model

```
Platform
├── Organization A (Manufacturing Company)
│   ├── Admin: configures connectors, agents, KBs
│   ├── Members: use published resources with own credentials
│   ├── Connectors (org-shared):
│   │   ├── SAP ERP (db connector)
│   │   ├── Seeyon OA (api connector)
│   │   └── Lark (msg connector, OAuth)
│   ├── Agents (org-shared):
│   │   ├── Finance Copilot        [connector: sap + lark]
│   │   ├── OA Approval Assistant   [connector: seeyon + built-in tools]
│   │   └── General AI Assistant    [tools: search, browser, code]
│   └── Knowledge Bases (org-shared, tagged):
│       ├── Contract Regulations    [tags: contract, legal]
│       ├── Finance Policies        [tags: finance, audit]
│       └── Employee Handbook       [tags: HR, onboarding]
├── Organization B (Tech Company)
│   ├── Connectors: Salesforce (api), Slack (msg, OAuth)
│   ├── Agents: CRM Agent, Internal Knowledge Agent
│   └── Knowledge Bases: Product Docs, API Reference
```

### Consider

> Items deferred from version roadmap. Priority labels indicate likelihood of future promotion.

**P2 — Worth building if the product direction supports it**

- [ ] **Cost-Aware Request Routing**: Skip full Agent loop for simple queries that pattern matching can resolve; local rule-based handling to reduce LLM cost. Inspired by Ruflo's 3-Tier routing (local transform → fast model → full model). *Deferred because: the LLM call IS the core product; fast_llm + ModelRegistry already handle cost optimization; pattern-matchable queries are rare in the Connector Hub use case. Re-evaluate when request volume scales up.*
- [ ] **Advanced KB Routing Strategy**: Multi-layer knowledge base selection beyond basic node-level binding. L1: static `kb_scope` per step (whitelist within agent's `kb_ids`); L2: priority hints with trigger conditions (e.g., "prefer contract regulations KB when legal clauses are involved"); L3: optional agent dynamic selection within whitelist for large KB pools (>5). *Deferred because: basic node-level KB binding in Blueprint Mode (v1.0) covers the primary enterprise use case. Advanced routing adds value only when KB pools are large and diverse enough to warrant automated selection. Re-evaluate after Blueprint Mode ships and real usage patterns emerge.*
- [ ] **Dify Workflow Migration**: Import Dify workflow export JSON and map to FIM Agent Blueprint templates — node type mapping (LLM → Action, Knowledge Retrieval → Action with KB scope, Code → python_exec, HTTP Request → connector, IF/ELSE → Condition), KB mapping wizard, variable mapping, migration report with clean/approximate/unsupported classification. *Deferred because: Blueprint Mode already serves as a Dify replacement for new pipelines. Migration tooling only matters when clients have a significant existing Dify workflow investment they refuse to rebuild. Build on demand when a concrete client migration project materializes.*

**P3 — Deferred indefinitely; already covered by existing primitives or being absorbed by providers**

- [ ] **Skills System**: Reusable multi-step capability units (e.g., "financial analysis" = retrieve + compute + format) that agents can share. *Reason: The existing primitive stack already covers this — Tool (atomic ops), Connector (external integration), Blueprint (multi-step pipelines), Agent (combines all). An Agent IS a skill bundle (prompt + tools + connectors + KB). Adding a Skill layer between Tool and Blueprint creates unnecessary concept overload (5 abstractions instead of 4) without clear user benefit. If sub-agent capability reuse becomes a real need, Blueprint sub-templates (sub-pipelines) can serve the same purpose without introducing a new entity. Re-evaluate only if users explicitly request sharing partial agent capabilities across agents.*

- [ ] **Conversation Summary Memory**: Automatic rolling summaries that persist across long sessions; hybrid window + summary strategy. *Reason: Largely overlaps with LLM Compact (shipped in v0.5). The compact mechanism already summarizes old turns via fast LLM; a separate summary memory adds marginal value.*
- [ ] **Multi-Agent Orchestration**: Leader/Worker agent pool, task graph with dependency edges, inter-agent messaging, graceful shutdown, token economics tracking. *Reason: LLM providers are building this natively — OpenAI Swarm, Anthropic Claude Code Teams, Google A2A protocol. The orchestration layer is commoditizing at the provider level; competing with first-party implementations is not a sustainable differentiator.*
- [ ] **Semantic Memory Store**: Cross-conversation knowledge extraction and retrieval; agent remembers facts/preferences across sessions via embedding-based lookup. *Reason: Model context windows are growing rapidly (Gemini 2M+, Claude 200K+), reducing the need for external memory management. Meanwhile, providers are adding native memory/personalization features (ChatGPT Memory, Claude Projects). The gap that semantic memory fills is shrinking with each model generation.*
- [ ] **Memory Lifecycle**: TTL-based expiry, importance scoring, explicit forget/remember commands. *Reason: Same as Semantic Memory Store — as models gain longer context and native memory capabilities, building a custom memory lifecycle system risks becoming redundant engineering effort.*

---

Contributions and ideas are welcome -- open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

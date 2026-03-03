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
- [x] **Conversational Interrupt**: Send messages while the agent is running — injected at the next iteration boundary without interrupting execution. Backend async queue per conversation; ReAct drains between iterations (JSON mode) or after tool results (native mode); DAG drains between phases. Messages persist as `message_type="inject"` for history replay. Frontend: input box switches to inject mode during execution (Send = inject, empty + click = Stop); optimistic UI with SSE confirmation and recall support

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

### v0.7 -- Organization, Connector Distribution & OAuth

> *"Who uses what — then distribute everywhere"*
>
> Organization is the foundation for enterprise deployment. Admin configures connectors, agents, and knowledge bases, then publishes to org members. Combined with connector distribution and OAuth, this version makes FIM Agent deployable to enterprise teams — like OpenAI / Anthropic Console's org model.

**Organization & Resource Sharing**
- [ ] **Organization Model**: Org CRUD (create, rename, delete); member invite (email or invite link) and remove; two roles — **admin** (full configuration: create/edit/publish connectors, agents, KBs, manage members) and **member** (use published resources, manage own conversations and credentials). Replaces current flat JWT auth with org-scoped access.
- [ ] **Resource Visibility**: Connector / Agent / KB gain `org_id` + `visibility` field (`personal` | `org-shared`). Admin publishes resources to org; members see org-shared resources alongside their personal ones. Published resources are read-only for members (use, not edit).
- [ ] **KB Tags**: Tag-based categorization for knowledge bases; org members can filter/discover KBs by tag (e.g., "contract", "finance", "HR", "regulations"); tags managed by admin, displayed as filterable chips in KB list
- [ ] **Frontend — Organization Management**: Org settings page (name, logo, invite link), member list with role badges, invite flow, resource publish/unpublish toggle on Connector/Agent/KB detail pages; org switcher in top nav (for users in multiple orgs)

**Connector Distribution**
- [ ] **Connector Export/Import**: Export JSON/YAML (without credentials), others can import
- [ ] **Fork Mechanism**: Create a copy from a published Connector, freely modify actions
- [ ] **Connector Versioning**: Track changes, support rollback
- [ ] **Official Connector Library**: GitHub, Lark, Slack and other preset connectors
- [ ] **MCP Server Export**: Generate standalone FastMCP Server code from Connector; users can run independently or fork

**OAuth 2.0**
- [ ] **OAuth 2.0 Support**:
  - OAuth configuration (client_id, client_secret, auth_url, token_url, scopes)
  - User clicks "Connect" → OAuth authorization page → callback → store refresh_token
  - Automatic token refresh (silent refresh before expiry)
  - Works with Organization: admin configures OAuth app credentials at org level, each member authorizes individually

### v0.8 -- Database Connector, Message Push & Governance

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

  **Relationship to other gates (all in v0.8):**
  - Confirmation Gate: **hardcoded safety** — connector write operations require user permission (allow once / allow always / deny)
  - Plan Preview: **explicit user review** — user opts in to review every DAG plan before execution
  - Soft Gate: **intelligent safety net** — agent self-triggers only when uncertain, zero config overhead

  **Implementation:**
  - SSE event `gate_required` with `{type: "soft_gate", options: [...], confidence: [...], timeout: 30}`
  - User response via POST `/api/chat/{id}/gate_response`
  - Agent config: `soft_gate_threshold: float` (default 0.5), `soft_gate_timeout: int` (default 30s)
  - No changes to DAG schema or tool bindings — Soft Gate is an execution-time behavior, not a configuration-time concept

### v0.9 -- Blueprint Mode & Production Hardening

> *"A familiar visual shell for enterprise clients, then harden for production"*
>
> Enterprise clients accustomed to Dify-style visual workflows need a controllable, auditable execution model before adopting AI agents. Blueprint Mode provides the static pipeline shell — drag-and-drop nodes, per-node KB/tool/prompt binding, IF/ELSE branching — while the agent handles dynamic reasoning within each node's scope. Combined with sandbox hardening and observability, this version makes FIM Agent enterprise-deployable.

**Blueprint Mode (Static Pipeline + Dynamic Execution)**

- [ ] **Visual Pipeline Builder**: Lightweight drag-and-drop editor for defining static execution pipelines — a Dify-equivalent visual workflow that enterprise clients can adopt without fear of uncontrollable AI behavior:
  - Canvas with draggable nodes and connectable edges
  - Node types: Action (tool/agent execution), Gate (guided HITL dialog from v0.8), Condition (if-else branching), Start/End
  - Per-node configuration: tool set binding, KB scope (which knowledge bases this node can access), system prompt / instruction template, model override, token budget
  - Conditional branches: rule-based (field value matching) or LLM-evaluated (confidence-based routing)
  - Pipeline saved as versioned JSON template; one template can be bound to multiple agents

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

- [ ] **Advanced KB Routing Strategy**: Multi-layer knowledge base selection beyond basic node-level binding. L1: static `kb_scope` per step (whitelist within agent's `kb_ids`); L2: priority hints with trigger conditions (e.g., "prefer contract regulations KB when legal clauses are involved"); L3: optional agent dynamic selection within whitelist for large KB pools (>5). *Deferred because: basic node-level KB binding in Blueprint Mode (v0.9) covers the primary enterprise use case. Advanced routing adds value only when KB pools are large and diverse enough to warrant automated selection. Re-evaluate after Blueprint Mode ships and real usage patterns emerge.*
- [ ] **Dify Workflow Migration**: Import Dify workflow export JSON and map to FIM Agent Blueprint templates — node type mapping (LLM → Action, Knowledge Retrieval → Action with KB scope, Code → python_exec, HTTP Request → connector, IF/ELSE → Condition), KB mapping wizard, variable mapping, migration report with clean/approximate/unsupported classification. *Deferred because: Blueprint Mode already serves as a Dify replacement for new pipelines. Migration tooling only matters when clients have a significant existing Dify workflow investment they refuse to rebuild. Build on demand when a concrete client migration project materializes.*

**P3 — Deferred indefinitely; being absorbed by LLM providers or already solved**

- [ ] **Conversation Summary Memory**: Automatic rolling summaries that persist across long sessions; hybrid window + summary strategy. *Reason: Largely overlaps with LLM Compact (shipped in v0.5). The compact mechanism already summarizes old turns via fast LLM; a separate summary memory adds marginal value.*
- [ ] **Multi-Agent Orchestration**: Leader/Worker agent pool, task graph with dependency edges, inter-agent messaging, graceful shutdown, token economics tracking. *Reason: LLM providers are building this natively — OpenAI Swarm, Anthropic Claude Code Teams, Google A2A protocol. The orchestration layer is commoditizing at the provider level; competing with first-party implementations is not a sustainable differentiator.*
- [ ] **Semantic Memory Store**: Cross-conversation knowledge extraction and retrieval; agent remembers facts/preferences across sessions via embedding-based lookup. *Reason: Model context windows are growing rapidly (Gemini 2M+, Claude 200K+), reducing the need for external memory management. Meanwhile, providers are adding native memory/personalization features (ChatGPT Memory, Claude Projects). The gap that semantic memory fills is shrinking with each model generation.*
- [ ] **Memory Lifecycle**: TTL-based expiry, importance scoring, explicit forget/remember commands. *Reason: Same as Semantic Memory Store — as models gain longer context and native memory capabilities, building a custom memory lifecycle system risks becoming redundant engineering effort.*

---

Contributions and ideas are welcome -- open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

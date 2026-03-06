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

## Strategy Decisions (2026-03)

> Key product decisions documented for roadmap alignment. Referenced by version sections below.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Open source vs. closed** | **Closed-source SaaS** (revisit post-PMF) | AI auto-configuration engine and legacy system connector know-how are the core moat; open-sourcing transfers that value to competitors. Open Core may be revisited once product-market fit is proven. |
| **Connector vs. MCP alignment** | **Keep separate; optional MCP export later** | Connector = no-code product feature (fill a form → tool); MCP = developer protocol (write code → server). They solve the same problem at different abstraction layers for different users. Don't merge; offer "Export as MCP Server" in v1.2. |
| **Frontend language** | **Chinese-first i18n** | Closed-source SaaS targeting Chinese enterprise (用友/金蝶/致远). Set up `next-intl` with `zh-CN` as primary, English as fallback from existing code. Code/commits/variable names stay English (engineering standard). |
| **Connector access modes** | **Three modes: API → DB → Auth-Login** | Priority order based on coverage and implementation complexity. API mode (80% of modern systems) polish first; DB mode (data analysis, high enterprise value) second; Auth-Login (legacy ERP multi-step credential exchange) third. See v0.8.1, v0.10. |
| **Core differentiator** | **AI auto-configuration** | "Give me any system's docs or credentials, AI figures out the rest" — not pre-built connectors (Zapier/n8n), not developer protocol (MCP), not workflow automation. The AI Connector Assistant quality is THE product. Closest competitors: Composio.dev (pre-built integrations for AI agents, no AI auto-config), MuleSoft/Boomi (enterprise iPaaS, requires engineers). |

**Three Connector Access Modes — Framework**

```
Mode 1: API Mode (shipped v0.6, polish in v0.8.1)
  Input: API documentation (OpenAPI / HTML / plain text)
  AI does: parse docs → generate Actions → auto-test → iterate until working
  Output: Connector with validated Actions
  Auth: Bearer / API key / Basic (static injection)

Mode 2: DB Mode (planned v0.10)
  Input: Database connection string (PostgreSQL / MySQL DSN)
  AI does: connect → explore schema → understand business semantics → suggest valuable queries
  Output: Connector with parameterized read-only SQL Actions
  Auth: Connection string credentials
  Safety: Read-only by default, writes require Confirmation Gate

Mode 3: Auth-Login Mode (planned v0.10)
  Input: Login URL + username + password (legacy ERP systems)
  AI does: detect auth flow → login → extract token → configure Bearer injection → handle refresh
  Output: Connector with session-managed auth
  Auth: Multi-step credential exchange (login → token → refresh cycle)
  Examples: 用友 NC Cloud, 金蝶云星空, 致远 OA
```

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
- [x] **KB URL Import**: Import web pages into a knowledge base by URL; Jina Reader fetches clean Markdown; multiple URLs supported (one per line); per-URL success/failed result reporting; "URL Import" tab in the KB upload dialog

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
- [x] **First-Run Admin Setup Wizard**: When the database has no users, `/login` redirects to `/setup` for initial admin account creation; `/setup` permanently redirects to `/login` once the admin exists

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

**Frontend**
- [x] OpenAPI import dialog (paste YAML/JSON or URL, reusable for both create and add-actions modes)
- [x] AI Action Panel: inline collapsible chat panel in action editor (generate → refine flow)
- [x] Conversational Connector Settings: chat-based form for creating and editing connectors and actions through dialogue
- [x] User Language Preference: `preferred_language` setting (auto/en/zh) with language directive injection across all LLM interactions; language selector in user dropdown
- [x] Conversational Agent Settings: port the conversational settings pattern to agent create/edit (same chat-based CRUD approach)
- [x] Agent Quick Chat Link: "Start Chat" shortcut on agent cards/detail page → navigates to `/new?agent={id}` with the agent pre-selected

**Validation**: Create Zhihe contract system Connector via conversation — provide API docs → Agent generates actions → test → publish.

**Example scenarios Connectors unlock:**
- **ERP / Finance** (SAP, Kingdee/金蝶, Oracle DB): Agent reads financial statements via DB Connector, generates analysis reports, pushes to Lark (飞书) / Slack
- **OA / Workflow** (Seeyon/致远, Weaver/泛微, REST API): Agent calls approval endpoints via API Connector, AI-assisted classification and routing, writes results back
- **CRM / Contracts** (Salesforce, custom PG): Agent reads contract clauses via API Connector, performs risk analysis, generates review opinions
- **Business DB** (MySQL / PG): Agent scans for anomalies via DB Connector, generates alerts, notifies responsible parties via Teams / WeCom (企微) / email

#### v0.6.3 — MCP Server Management UI (shipped)

- [x] **MCPServer ORM & API**: Per-user MCP server records (transport: stdio/sse, command/args/env or URL); full CRUD at `/api/mcp-servers` with JWT auth and ownership isolation
- [x] **SSE Transport**: `MCPClient.connect_sse()` for remote MCP servers; `MCPClient.disconnect()` for individual session removal
- [x] **Per-Request MCP Lifecycle**: Active user MCP servers connect on-demand in `_resolve_tools()` and disconnect after SSE stream ends; system-level `MCP_SERVERS` env var remains supported in parallel
- [x] **Tools Page (`/tools`)**: Built-in tool catalog (read-only, grouped by category) + MCP server management grid with add/edit/delete; transport-aware dialog (STDIO: command+args+env, SSE: URL)
- [x] **Streamable HTTP Transport**: MCP 2025-03-26 spec; `MCPClient.connect_streamable_http()` via `mcp.client.streamable_http`; 3rd transport toggle in dialog
- [x] **HTTP Headers for SSE/Streamable HTTP**: `headers` JSON field stored per server; passed at connect time; key-value editor in dialog (e.g. Authorization bearer tokens)
- [x] **STDIO Working Directory**: `working_dir` field → `StdioServerParameters.cwd`; optional input in dialog
- [x] **ALLOW_STDIO_MCP**: Env var guard blocking STDIO subprocess servers in SaaS mode; `GET /capabilities` endpoint for frontend feature detection

#### v0.6.4 — Admin Panel & UX Polish (shipped)

- [x] **Admin Panel**: `/admin` page with stats dashboard (users, conversations, messages, tokens, agents, KBs, 14-day activity bar chart, model usage donut chart) and full user management (search, pagination, create, edit, reset password, toggle admin, enable/disable)
- [x] **Role-Based Access**: `is_admin` / `is_active` on User model; first user auto-admin; disabled accounts blocked; `get_current_admin` dependency
- [x] **Agent Execution Mode**: Per-agent `execution_mode` (react/dag) sets default mode for new conversations; Standard/Planner toggle in agent settings
- [x] **Agent Temperature Control**: Per-agent temperature slider (0–2.0) stored in `model_config_json`
- [x] **Drag-and-Drop Suggested Prompts**: @dnd-kit-based reordering replaces arrow buttons
- [x] **Link Navigation**: All navigation buttons converted to `<Link>` for middle-click/Cmd+Click support
- [x] **Toast Feedback**: Sonner toast notifications for all CRUD success/error replacing `console.error()`
- [x] **Dirty State Protection**: MCP dialog and agent editor warn before discarding unsaved changes
- [x] **Mode Labels**: "ReAct" → "Standard", "DAG" → "Planner" across the UI
- [x] **Tools Page Tabs**: Built-in tools (card grid with category filters) and MCP Servers as separate tabs
- [x] **Retry Button**: Playground shows retry for stopped/interrupted tasks
- [x] **URL File Resolver**: Direct file download detection in KB URL import (PDF, DOCX, etc.)

#### v0.6.5 — Admin Panel v2: Connector Stats & Overview Enhancement (shipped)

- [x] **Connector Call Logging**: `connector_call_logs` table tracks every connector HTTP call (connector/action IDs, agent/conversation context, method, status, latency, sizes); callback pattern in ConnectorToolAdapter with automatic logging in chat endpoints
- [x] **Connector Stats API**: `GET /api/admin/connector-stats` endpoint — per-connector call counts, success rates, average latency, last-called timestamps
- [x] **Enhanced Admin Stats**: Overview stats endpoint extended with KB doc/chunk counts, connector count, today's conversations, and tokens-by-agent breakdown
- [x] **Admin Connectors Tab**: New tab in admin panel showing connector call metrics table (total calls, success rate, avg latency, last called)
- [x] **Tokens by Agent Chart**: Per-agent token consumption bar chart in admin overview dashboard
- [x] **Eager model_name Resolution**: Conversation `model_name` resolved at creation time (fixes "Unknown" in admin stats)

#### v0.6.6 — MCP Hub: Smithery Registry Search & One-Click Install (shipped)

- [x] **Smithery Registry Proxy**: `GET /api/mcp-servers/hub/search` proxies the public Smithery Registry API with auth guard, pagination, and optional keyword query
- [x] **MCPHubDialog**: Searchable dialog (400ms debounce) listing community MCP servers with verified badge, remote/local pill, install count, and icon
- [x] **Remote One-Click Install**: Remote (Smithery-hosted) servers install directly via Streamable HTTP transport (`https://server.smithery.ai/{qualifiedName}/mcp`)
- [x] **Local Pre-Fill Install**: Local (stdio) servers close the Hub dialog and pre-fill MCPServerDialog with `npx @smithery/cli@latest run {qualifiedName}` args
- [x] **Browse Hub Button**: Added to the MCP Servers tab header in `/tools` page, left of the existing "Add Server" button

#### v0.6.7 — System Model Config Management (shipped)

- [x] **ModelConfig Fields**: `max_output_tokens`, `context_size`, and `role` added to `ModelConfig` ORM and schemas; migrations `e5g7h9i1j234` + `f6h8j0k2l345`
- [x] **Role-Based LLM Slots**: `role: "general" | "fast" | null` on `ModelConfig`; system configs use `user_id=NULL`; `_unset_role()` enforces single-active per role; `get_system_llm_by_role()`, `get_effective_llm()`, `get_effective_fast_llm()` in `deps.py`
- [x] **Full Priority Chain Wired**: General — DB `role=general` → ENV; Fast — DB `role=fast` → DB `role=general` → ENV; agent `model_config_id` override at top; both ReAct and DAG endpoints use DB-resolved LLMs
- [x] **Model Provider Management UI**: Settings → Models with General/Fast role slot cards (RoleSlot + AssignRoleDialog); provider list with Brain/Zap role badges; ProviderDialog includes Role selector; `modelApi.setRole()` in frontend
- [x] **Agent Model Selector**: Agent settings form exposes Model dropdown; stored as `model_config_json.model_config_id`

#### v0.6.8 — Utility Built-in Tools (shipped)

- [x] **EmailSendTool** (`email_send`): SMTP email sending; SSL/STARTTLS/plain; auto-registered from `SMTP_HOST`/`SMTP_USER`/`SMTP_PASS` env vars; `SMTP_FROM` + `SMTP_FROM_NAME` optional
- [x] **JsonTransformTool** (`json_transform`): JMESPath-based JSON extraction and transformation; no extra dependencies
- [x] **TemplateRenderTool** (`template_render`): Jinja2 template rendering with variable injection; `jinja2>=3.0` added as core dependency
- [x] **TextUtilsTool** (`text_utils`): String manipulation — trim, split, join, replace, case conversion, count, word count, truncate, pad
- [x] **Tool catalog category ordering**: `"general"` always first; Zap icon + yellow color for the general category in Built-in Tools UI

---

### v0.7 -- Admin Platform (shipped)

> *"Operational controls for multi-user deployments"*
>
> Self-hosted multi-user deployments need more than user CRUD — admins need token controls, visibility into integrations, conversation oversight, invite-only access, storage tracking, per-user session control, and org-wide tools.

**User & Access Control**
- [x] **Per-User Token Quota**: Monthly token cap per user; system-wide default; 429 enforcement in chat endpoints (ReAct + DAG); monthly usage displayed in admin user list
- [x] **Per-User Force Logout**: Invalidate a single user's refresh token without affecting other sessions; audit logged
- [x] **Invite Code Registration**: Three-mode registration (`open` / `invite` / `disabled`); admin generates 8-char alphanumeric codes with optional note, max-uses cap, and expiry; invite code CRUD with revoke; backward-compatible with legacy `registration_enabled` setting

**Visibility & Moderation**
- [x] **API Integration Health Dashboard**: Read-only endpoint checking env-var presence for Main LLM, Fast LLM, Embedding, Reranker, Web Search, Web Fetch, Image Generation; colored dot UI in Admin Overview
- [x] **Conversation Moderation**: Admin can list all users' conversations (paginated, searchable by user or title) and delete any conversation with cascade (messages + uploads); audit logged
- [x] **Storage Management**: Per-user disk usage summary (file count + bytes); clear a user's upload directory; clean orphaned conversation upload dirs for deleted conversations; audit logged

**Infrastructure**
- [x] **Global MCP Servers**: Admin-provisioned MCP servers visible to all users in chat sessions; full CRUD + test endpoint in admin panel; loaded alongside user-owned servers in tool resolution

---

### v0.8 -- SaaS Runtime & Provider Abstraction

> *"Secure execution and pluggable services for multi-tenant deployment"*
>
> SaaS deployment means untrusted code from multiple users runs on shared infrastructure. The in-process python_exec sandbox (module blocklist + timeout) is a critical security gap — container-level or microVM isolation is required. Meanwhile, hardcoded service providers (Jina Search/Fetch) must become pluggable for global availability and provider flexibility.

**Sandbox Execution Backend**
- [x] **Code Execution Sandbox**: Pluggable `SandboxBackend` Protocol with `LocalBackend` (in-process, zero dependencies) and `DockerBackend` (OS-level isolation via Docker containers — `--network=none`, `--memory=256m`, `--cpus=0.5`); configured via `CODE_EXEC_BACKEND=local|docker`; `DOCKER_PYTHON_IMAGE`, `DOCKER_NODE_IMAGE`, `DOCKER_SHELL_IMAGE` to override default images
- [x] **Sandbox Lifecycle Management**: Resource limits (CPU, memory, execution timeout) configurable per agent via `sandbox_config` JSON on Agent model; global defaults via `DOCKER_MEMORY`, `DOCKER_CPUS`, `SANDBOX_TIMEOUT` env vars; per-agent overrides flow through `discover_builtin_tools(sandbox_config=...)` → exec tools → `DockerBackend`; `LocalBackend` ignores limits (in-process). Container pooling/reuse deferred to future.
- [x] **Shell Exec Sandboxing**: `shell_exec` routes through the sandbox backend; Docker mode executes shell commands inside an isolated container, not on the host
- [x] **Multi-Language Execution**: `node_exec` tool adds JavaScript/Node.js execution (local: `node -e`; Docker: `node:20-slim`); Python and shell already covered; TypeScript planned

**Service Provider Abstraction**
- [x] **Web Search Adapter**: `BaseWebSearch` protocol with pluggable implementations — Jina Search (default, free tier), Tavily, Brave Search; configured via `WEB_SEARCH_PROVIDER` env var; graceful fallback chain
- [x] **Web Fetch Adapter**: `BaseWebFetch` protocol — Jina Reader (default), self-hosted httpx + html2text fallback; configured via `WEB_FETCH_PROVIDER` env var
- [x] **Reranker Providers**: Add Cohere Reranker and OpenAI-compatible reranker implementations alongside existing Jina; configured via `RERANKER_PROVIDER` env var; `BaseReranker` interface already exists, only new implementations needed

#### v0.8.1 — Connector Intelligence & Chinese-First i18n

> *"Sharpen the core product: AI auto-configuration quality and Chinese enterprise readiness"*
>
> Two priorities before Organization (v0.9): make the AI Connector Builder production-quality for the API access mode, and ship Chinese UI for enterprise demos. These are the highest-leverage items for the target market (Chinese enterprise SaaS).

**Connector Response Intelligence**
- [x] **Schema-Hint Truncation**: Shared `truncate_tool_output()` utility (`core/tool/truncation.py`) for both ConnectorToolAdapter and MCPToolAdapter; truncation messages now include JSON key lists and array item counts so the agent knows what was omitted and can refine queries — replaces blind `[Truncated -- N chars]` messages. MCPToolAdapter previously had **zero truncation**; now protected against oversized tool outputs (e.g., 265K-char MCP responses that would blow up the context window). Inspired by Claude Code's overflow mechanism: when tool output exceeds limits, provide a "map" (schema hint) not the "territory" (raw data).

**AI Connector Assistant Enhancement (API Mode)**

> The AI Connector Assistant (v0.6.2) can generate Actions from OpenAPI specs and natural language. This iteration makes it robust enough to handle real-world enterprise API documentation — which is often HTML pages, PDFs, or unstructured text, not clean OpenAPI specs.
>
> **This is the core product differentiator.** The quality of this assistant determines whether "give me your API docs and I'll configure everything" is a real promise or a demo trick.

- [ ] **HTML/Text API Doc Parsing**: AI assistant reads raw HTML documentation pages and unstructured text (not just OpenAPI/Swagger specs); extracts endpoints, parameters, auth requirements, and generates Action configs. Many enterprise systems (用友, 金蝶) only provide HTML docs or Word documents, never OpenAPI.
- [ ] **Action Auto-Test & Iteration**: After generating an Action, the assistant automatically executes a test call against the real API; if the call fails (4xx/5xx), it analyzes the error response and iteratively adjusts parameters, headers, or body until the Action works or reports what's blocking it. This is the difference between "generated a config" and "generated a working config."
- [ ] **Auth Flow Detection**: AI identifies authentication requirements from API docs (Bearer token, API key, OAuth, session-based login) and pre-configures the Connector's `auth_type` and `auth_config`. Currently admins must manually select auth type and fill in config — AI should handle this.
- [ ] **Batch Action Generation**: From a single API documentation page/site, generate multiple related Actions at once (e.g., "CRUD for contacts" → 4-5 Actions), with consistent naming and parameter conventions. Currently only one Action generated at a time.

**Frontend i18n (Chinese-First)**

> Closed-source SaaS targeting Chinese enterprise market. Chinese UI is a prerequisite for demos, sales, and pilot deployments. English fallback from existing code is sufficient — no separate English translation effort.
>
> **Rule: Code stays English, UI speaks Chinese.** Variable names, commit messages, comments, technical docs, CHANGELOG — all remain English. Only user-facing UI strings are externalized to locale files.

- [ ] **i18n Infrastructure**: `next-intl` integration with `zh-CN` as primary locale, `en` as fallback (existing English strings serve as fallback keys — no translation file needed for English)
- [ ] **UI Text Externalization**: All user-facing strings extracted to locale JSON files (`messages/zh-CN.json`); components use `useTranslations()` hook. Organized by page/feature (e.g., `agent.create`, `connector.authType.bearer`, `admin.userManagement`)
- [ ] **Locale Resolution**: Driven by user's `preferred_language` setting (already exists in backend); `auto` mode falls back to `navigator.language`; switching language does not require page reload (next-intl handles this)

**NOT in this version (explicit decisions — see Strategy Decisions section):**
- ❌ Connector does NOT adopt MCP protocol internally — Connector is a no-code product, MCP is a developer protocol; they are complementary layers serving different users
- ❌ No full English i18n translation effort — English fallback from existing code is sufficient; investing in English translation has zero ROI for the Chinese enterprise target market
- ❌ No open source release — closed-source SaaS confirmed; the AI auto-configuration engine and legacy system know-how are the moat
- ❌ No DB mode or Auth-Login mode yet — these are v0.10 scope; API mode must be production-quality first

---

### v0.9 -- Organization, Connector Distribution & OAuth

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
- [ ] **Official Connector Library**: High-quality pre-built connectors for the most common enterprise systems; each ships with validated auth config, action schemas, and usage examples; all published to the Connector Marketplace (v1.2) as reference implementations:
  - **Domestic (China)**: Lark (飞书) — messages, docs, calendar, approval bots; WeCom (企业微信) — messages, customer service; Kingdee ERP (金蝶) — financial records, orders; Seeyon OA (致远) — approval workflow, process query; Yonyou ERP (用友) — financial data; FineReport (帆软) — report export
  - **International**: GitHub — repos, issues, PRs; Slack — messages, channels, search; Salesforce — leads, contacts, opportunities; Notion — pages, databases, blocks; Jira — issues, sprints, projects; HubSpot — contacts, deals, emails; Stripe — payments, subscriptions
  - **Infrastructure**: SMTP Email, Webhook (generic HTTP push/pull), Postgres template (official DB connector example)
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

**Conversation Channels**

> *"Bring agents into messaging apps — users talk to your agent from where they already are"*

- [ ] **Channel Adapter Architecture**: `channels/` module with `BaseChannel` abstraction; single webhook router `POST /webhooks/{channel}`; channels are code-defined (not DB-driven), credentials stored in env/DB; startup auto-registers webhooks for configured channels
- [ ] **Telegram Channel**: Configure bot token via env (`TELEGRAM_BOT_TOKEN`); private chat only (group messages ignored); inline keyboard agent picker on `/start` — lists all published agents; per-user session tracks selected agent; typing indicator (`sendChatAction`) during processing; full ReAct/DAG execution on backend, only final answer delivered to user
- [ ] **Feishu Bot Channel**: Feishu app webhook (`FEISHU_APP_ID` / `FEISHU_APP_SECRET`); private message + group @mention; agent picker via `/agents` slash command; leverages Feishu Connector for API tool calls — same agent can both receive Feishu messages and call Feishu APIs

### v0.10 -- Database Connector, Auth-Login, Message Push & Governance

> *"Not just APIs — databases, legacy systems, notifications, and operational safety"*
>
> This version completes the **Three Connector Access Modes** framework (see Strategy Decisions). API Mode shipped in v0.6 and polished in v0.8.1. DB Mode and Auth-Login Mode ship here, making FIM Agent capable of connecting to virtually any enterprise system — modern API-first SaaS, traditional databases, and legacy ERP/OA systems that only expose login-based access.

**Connector Access Mode 2: Database Connector**
- [ ] **Database Connector Type**:
  - Support PostgreSQL, MySQL (asyncpg / aiomysql)
  - Schema introspection: AI browses table structure, understands business semantics (e.g., `bd_corp` → company, `gl_voucher` → financial voucher), suggests useful queries
  - Action = parameterized SQL query (read-only by default, writes require Confirmation Gate)
  - Connection pool management, query timeout protection
  - **Compliance**: read-only by default; write operations require explicit `requires_confirmation: true`; audit logging for all queries; row-level result limits to prevent full table dumps
  - **AI Assistant for DB Mode**: Give AI the connection string → it runs `SHOW TABLES` / `\dt` → explores column types and sample data → recommends which tables/views are valuable → auto-generates parameterized query Actions

**Connector Access Mode 3: Auth-Login (Legacy Enterprise Systems)**
- [ ] **Multi-Step Credential Exchange**:
  Legacy enterprise systems (用友/Yonyou NC Cloud, 金蝶/Kingdee Cloud Cosmic, 致远/Seeyon OA, 泛微/Weaver e-cology) often require multi-step authentication that goes beyond simple Bearer/API-key injection:
  - New `auth_type: "login"` on Connector with `login_config` specifying: login URL, credential fields, token extraction path (JMESPath), and optional token TTL
  - Login action: `POST /api/auth/login {username, password}` → extract `{token, session_id}` from response via JMESPath
  - Auto-inject extracted token into subsequent Action calls as Bearer/Cookie header
  - Token refresh: detect expiry via TTL countdown or 401 response → re-run login action → update cached token transparently
  - Support complex auth flows: public key fetch → encrypt password → login → session_id + token (e.g., Yonyou NC Cloud pattern)
  - AI Assistant can detect and configure auth flows from API documentation (builds on v0.8.1 Auth Flow Detection)
  - **Why this matters**: These legacy systems serve millions of Chinese enterprises. If FIM Agent can connect to 用友/金蝶/致远 with just a username and password, the sales pitch becomes trivially simple: "Give me your ERP login, I'll give you an AI assistant that understands your financial data."

**Connector Types (continued)**
- [ ] **Message Push Connector Type**:
  - Send results to Lark / WeCom / Slack / email / webhook
  - Templated message formatting
  - Can serve as Agent's "output channel"
- [ ] **Async Query Connector Type**:
  Long-running data operations where synchronous wait is impractical (Hadoop/Hive queries, Spark jobs, large data exports):
  - Submit query → receive `job_id`; poll status at configurable interval; fetch results when `status = completed`
  - Supports incremental progress reporting if target API provides completion percentage
  - Configurable `poll_interval` (default 5s) and `max_wait` (default 5 min) per action; timeout returns partial result or structured error
  - `async: true` flag in action schema enables polling behavior; `status_path` + `result_path` JMESPath expressions extract status/result from polling response
  - Compatible with Confirmation Gate — the submit step can require user approval before the job starts

**Hub Cross-Connector Orchestration**

> *The core value of Hub mode — connectors talking to each other through AI*

Hub mode enables cross-system workflows where the agent reads from one connector, processes the result, and writes to another — with Confirmation Gate governing every write hop. No special primitives needed: this is standard ReAct/DAG execution over multiple connectors bound to the same agent.

```
ERP Connector     (read financial data)
    → Agent       (analyze, detect anomalies, generate report)
    → OA Connector      (submit approval request)
    → IM Connector      (notify team via Lark / WeCom)
    → Email Connector   (send PDF summary to stakeholder)
```

- **Connector chain visualization**: Agent settings show a data-flow preview when multiple connectors are bound — helps admin verify the orchestration topology before deployment
- **Data mapping between connectors**: `json_transform` (JMESPath) and `template_render` (Jinja2) — both already shipped — serve as the schema translation and formatting layer between connectors with incompatible data structures
- **Error propagation**: When one connector in a chain fails, the agent evaluates whether to abort, retry, or compensate based on error type and Confirmation Gate decisions
- **Cross-connector tracing**: All connector calls in a single conversation are linked by `conversation_id` in `connector_call_logs` — the full cross-system chain is auditable end-to-end

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

  **Relationship to other gates (all in v0.10):**
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
  - Node types: Action (tool/agent execution), Gate (guided HITL dialog from v0.9), Condition (if-else branching), Start/End
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
  Selected nodes upgrade from fixed prompt templates to agent-mode execution — the node scope (tool set, KB whitelist, token budget) is still statically defined, but within those boundaries the agent can reason, select tools, and iterate. Non-critical nodes remain fully static. Soft Gate (v0.9) activates for agent-mode nodes to handle uncertainty.

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

**i18n** *(core items moved to v0.8.1 — Chinese-first strategy)*
- [x] **User Language Preference**: `preferred_language` backend setting with language directive injection across all LLM interactions *(shipped in v0.6.2)*
- [ ] **Frontend i18n**: → **moved to v0.8.1** (Chinese-first; `next-intl` + `zh-CN` primary locale)
- [ ] **Documentation i18n**: Chinese user-facing documentation (help center, onboarding guides); technical docs (CHANGELOG, API docs) remain English. Deferred until product stabilizes post-v0.9.

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
- [ ] **Connector Marketplace**: Searchable in-platform catalog of published connectors — browse by category, search by name/keyword, view install count, ratings, and author; one-click install into your workspace
- [ ] **Plugin System**: Pip-installable Connector packages with entry-point auto-registration; packages include connector definitions, default auth schema, and bundled test fixtures

**Enterprise Operations**

- [ ] **Admin Dashboard**: Audit logs, Connector management, health monitoring, usage/cost analytics
- [ ] **Scheduled Jobs**: Cron triggers, webhook triggers — run agents on schedule or in response to external events
- [ ] **Batch Execution**: Run an agent against multiple inputs in one job (e.g., review 100 contracts, audit 50 invoices); progress tracking, partial failure handling, result aggregation
- [ ] **Enterprise Security**: Data encryption, IP whitelisting, SOC2 audit logging
- [ ] **PostgreSQL**: Optional for large-scale deployments

### v1.2 -- Open Connector Ecosystem & SaaS Marketplace

> *"Turn connectors into a community currency"*
>
> Every major integration platform has a marketplace moment — Zapier's App Directory, Salesforce AppExchange, Slack App Directory. FIM Agent's differentiator is that connectors are AI-native (not just OAuth flows) and community-extensible. Any developer or ISV can contribute a connector, publish it to the global registry, and optionally monetize it via per-call billing. The platform becomes the standard for AI-to-system integration.

**Connector Registry (Global)**

- [ ] **Community Connector Registry**: Centralized registry where developers publish connector definitions (JSON/YAML, no credentials); searchable by system name, category (ERP/CRM/OA/DB/IM/Finance), region, and compatibility version
- [ ] **Contributor System**: GitHub-linked developer accounts; connector authorship and attribution; version history and changelog per connector; verified badge for official and partner connectors
- [ ] **One-Click Install**: Browse registry → install connector into workspace → configure credentials → bind to agent; zero manual JSON editing; installed connectors sync to personal/org workspace automatically
- [ ] **Fork & Customize**: Fork any published connector → private copy in your workspace → modify actions, auth schema, defaults → optionally re-publish as a derivative

**Ecosystem Business Model**

- [ ] **Free Tier**: All community-contributed connectors are free; platform takes no cut; contributors build reputation and distribution
- [ ] **Paid Connectors (ISV tier)**: ISVs (system integrators, SaaS vendors) can publish premium connectors with per-call billing — e.g., "Gold Salesforce Connector: $0.001/call" or "Certified SAP Connector: $50/month/workspace"; platform handles billing, usage metering, and revenue split (e.g., 70/30)
- [ ] **Connector Certification Program**: Connector authors apply for "Certified" status — platform team reviews schema quality, auth security, action coverage, and test coverage; certified connectors receive priority placement and verified badge
- [ ] **Official Partner Program**: Major SaaS vendors (Salesforce, Lark, WeCom, HubSpot) co-develop and co-maintain official connectors with the platform team; bidirectional marketing and technical partnership

**Connector Standard**

- [ ] **Connector Specification v1.0**: Formal, versioned specification document for the FIM Connector format — auth types, action schema, parameter types, error codes, async protocol, response truncation contract; enables third-party tooling (validators, generators, importers)
- [ ] **OpenAPI ↔ Connector Bidirectional Bridge**: Export any connector as OpenAPI spec; import any OpenAPI spec as connector (already partially shipped); lossless round-trip enables interoperability with the broader API ecosystem
- [ ] **MCP Connector Bridge**: Auto-generate a standalone MCP server from any Connector in the registry; bridges the FIM Connector ecosystem with the growing MCP tool ecosystem
- [ ] **Connector SDK**: Python library (`fim-connector-sdk`) with helpers for auth, pagination, error handling, and testing; CLI for scaffolding, local testing, and publishing to the registry (`fim connector new`, `fim connector publish`)

**Validation**: A community developer creates a connector for a domestic OA system using the SDK → publishes to registry → another user installs in one click → binds to agent → cross-connector workflow (OA → IM notification) works end-to-end without any platform code changes.

---

## Connector Standardization Levels

| Level | Version | Approach |
|-------|---------|----------|
| **Level 1** | v0.6 | Manual/AI-created Connector, DB storage, runtime HTTP proxy |
| **Level 2** | v0.9 | Export/Import + Fork, MCP Server export |
| **Level 3** | v1.1 | Upload OpenAPI spec, AI auto-generates complete Connector |
| **Level 4** | v1.2 | Open registry, community contributions, ISV marketplace, Connector SDK, bidirectional OpenAPI/MCP bridge |

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

**P1 — Strong fit with product direction; revisit when bandwidth allows**

- [ ] **Browser Automation**: Playwright-based browser control — navigate, click, fill forms, take screenshots, extract text, evaluate JS, manage sessions. Enables agents to interact with login-gated websites, JavaScript-rendered pages, and multi-step web workflows. Screenshots stream inline as execution audit trail. *Architecture choice: implement as built-in tool suite (10+ granular tools matching competitor parity) or expose via MCP Playwright server (zero backend dependency). Built-in approach gives deeper DAG integration and screenshot-in-step-output UX; MCP approach keeps core lean. Revisit when the use case (web automation vs. API-first Connector Hub) becomes clearer from user demand.*

**P2 — Worth building if the product direction supports it**

- [ ] **Model Management UI**: Visual interface for managing LLM/Embedding model configurations — add/edit/delete model providers (OpenAI, Anthropic, Gemini, Ollama, etc.), set default model, configure Fast LLM separately. Backend ModelRegistry already exists (v0.2); this is purely a UI layer on top of current env-var config. Also includes per-agent model override (bind a specific model to an agent instead of the global default) and Embedding model provider selection (decouple from Jina-only). *Deferred because: env-var config works fine for self-hosted single-tenant deployments; UI only becomes necessary for multi-user/org deployments where admins need to configure models without touching `.env`. Re-evaluate when Organization (v0.9) ships.*

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

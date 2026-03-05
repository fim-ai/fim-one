# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions correspond to [Roadmap](Roadmap.md) milestones.

## [Unreleased]

### Added
- **Admin Panel**: System stats dashboard (total users, conversations, messages, tokens, agents, KBs) with recharts bar chart (14-day activity) and donut chart (model usage distribution); user management with search, pagination, create user, edit profile, reset password, toggle admin role, and enable/disable account; admin-only access via `get_current_admin` dependency
- **Role-Based Access Control**: `is_admin` and `is_active` fields on User model; first registered user auto-promoted to admin; disabled accounts blocked from login, token refresh, and API access; `get_current_admin` FastAPI dependency for admin-only endpoints
- **Agent Execution Mode**: Per-agent `execution_mode` field (react/dag) stored in DB; sets default mode for new conversations; frontend mode toggle (Standard/Planner) with icon and description in agent settings form
- **Agent Temperature Control**: Per-agent temperature slider in settings form (0.00–2.00 with Reset to Default); stored in `model_config_json`; frontend syncs execution mode from agent on selection
- **Drag-and-Drop Suggested Prompts**: Suggested prompts editor rebuilt with @dnd-kit for drag-and-drop reordering (replaces arrow up/down buttons); stable IDs prevent React key issues during reorder
- **URL File Resolver**: `resolve_url()` in url_importer detects file extensions (.pdf, .docx, .xlsx, etc.) for direct binary download vs Jina Reader for web pages; enables KB import of direct file URLs
- **BaseLLM.model_id Property**: New property on BaseLLM (returns None); OpenAICompatibleLLM overrides to return the actual model identifier
- **Streamable HTTP MCP Transport**: MCP servers can now use the MCP 2025-03-26 Streamable HTTP transport (`transport: "streamable_http"`); `MCPClient.connect_streamable_http()` using `mcp.client.streamable_http`; UI shows a 3rd transport option with HTTP-aware URL placeholder
- **MCP HTTP Headers**: SSE and Streamable HTTP servers support `headers` (dict of HTTP headers, e.g. `Authorization: Bearer token`); stored as JSON column, passed at connect time; HTTP Headers key-value editor in the MCP server dialog
- **STDIO Working Directory**: STDIO MCP servers now accept `working_dir`; passed as `cwd` to `StdioServerParameters`; optional field in dialog and schema
- **ALLOW_STDIO_MCP Guard**: New `ALLOW_STDIO_MCP` env var (default `true`); set to `false` in SaaS deployments to block subprocess MCP servers; `GET /api/mcp-servers/capabilities` endpoint exposes the flag to the frontend; STDIO option disabled with warning when false
- **MCP Server Management UI**: New `/tools` page with per-user MCP server CRUD (name, transport STDIO/SSE, command/args/env or URL); servers connect on-demand during agent execution and disconnect after the SSE stream ends; system-level `MCP_SERVERS` env var continues to work alongside user-defined servers
- **MCPClient SSE Transport**: `MCPClient.connect_sse()` for remote MCP servers over HTTP/SSE transport, alongside existing `connect_stdio()`; `MCPClient.disconnect()` for single-server session removal
- **Built-in Tools Catalog**: Read-only tools reference in `/tools` page showing Computation, Web, Filesystem, and Knowledge categories
- **KB URL Import**: New "URL Import" tab in the KB upload dialog; users paste multiple URLs (one per line), backend fetches each via Jina Reader API (r.jina.ai) and ingests as Markdown documents using the existing chunking/embedding/LanceDB pipeline; per-URL success/failed status reported
- **file_ops Extended Operations**: Nine new file operations added to the `file_ops` built-in tool — `append`, `delete`, `exists`, `get_info`, `read_json`, `write_json`, `read_csv`, `write_csv`, `find_replace` — bringing feature parity with competitor file tooling while retaining the single-tool dispatch architecture
- **list_knowledge_bases Tool**: New `knowledge`-category built-in tool that lets agents discover all available knowledge bases (id, name, description, document/chunk counts) before calling `kb_retrieve`; auto-discovered via `discover_builtin_tools()`
- **Suggested Prompts Editor**: Agent configuration form now includes a suggested prompts editor; prompts displayed as clickable cards on the playground page
- **Streaming Loading Indicator**: Visual loading state for streaming responses in the playground
- **New Chat Keyboard Shortcut**: ⇧⌘O (Mac) / Ctrl+Shift+O (Windows) to start a new chat from anywhere
- **User Language Preference**: `preferred_language` setting (auto/en/zh) with language directive injection across all LLM interactions (ReAct, DAG, Connector AI, suggestions)
- **Language Selector**: Language sub-menu in user dropdown menu for quick switching
- **OpenAPI Spec Import**: One-shot connector creation from OpenAPI 3.x specs (JSON/YAML); paste, URL, or parsed dict input; auto-extracts connector name, base_url, and description from spec info/servers; parses all operations into actions with parameters_schema, method, path, and requires_confirmation; resolves local `$ref` pointers
- **Add Actions from OpenAPI**: Import actions from OpenAPI spec into an existing connector; optional "replace existing" mode
- **AI Action Generation**: LLM-powered action creation from natural language instructions; connector context + existing actions fed to fast LLM; per-action Pydantic validation with partial success reporting
- **AI Action Refinement**: LLM-powered action editing via create/update/delete operations; supports targeted single-action or bulk refinement
- **AI Action Panel**: Inline collapsible chat panel in the action editor; first message generates, follow-ups refine; live result display (created/updated/deleted counts)
- **OpenAPI Import Dialog**: Reusable dialog with Paste/URL tabs; two modes — "create" (one-shot) and "add-actions" (to existing connector)
- **Citation Badges & References Redesign**: Inline `[N]` citation badges as styled `<sup>` elements in markdown; references section with card layout and collapse/expand toggle
- **KB Document Retry**: Failed document ingestion can be retried; frontend retry button with error tooltip
- **Chunk Text Search**: Search/filter chunks within KB drawer with debounced input and match highlighting
- **Connector Smart Truncation**: JSON-aware response truncation (array item limit + char limit, env-configurable via `CONNECTOR_RESPONSE_MAX_CHARS` / `CONNECTOR_RESPONSE_MAX_ITEMS`)
- **Observation JSON Highlighting**: Auto-detect JSON in tool output, pretty-print with syntax highlighting; label renamed to "Output"
- **Sonner Toast Notifications**: Toast notification library for non-blocking user feedback
- **KB Document Management in README**: Added KB Document Management to README Key Features section
- **RETRIEVAL_MODE Config**: Switch between grounding pipeline and basic RAG via `RETRIEVAL_MODE` env var (grounding | simple); simple mode uses `kb_retrieve` for basic RAG, grounding mode uses `grounded_retrieve` for full 5-stage pipeline; `KBRetrieveTool` supports bound kb_ids with multi-KB merge; frontend `parseSimpleEvidence()` for simple mode references
- **Agent Quick Chat Link**: "Start Chat" button on agent cards and detail page for published agents; auto-selects agent from `?agent=` URL param in chat interface
- **Documentation i18n (Roadmap)**: Added Chinese translation for README, Wiki, and GitHub pages as a v1.0 i18n milestone
- **Iteration Duration Tracking**: Server-side per-iteration elapsed timing (`iter_elapsed`) in DAG executor; frontend displays duration on completed iterations with client-side fallback
- **Elapsed Timer for Running Steps**: Real-time running clock on active DAG step cards
- **First-Run Admin Setup Wizard**: When the database is empty, visitors are redirected from `/login` to `/setup` to create the initial admin account (`is_admin=True`); once created, `/setup` permanently redirects to `/login`
- **Connector Call Logging**: `connector_call_logs` table (ORM + Alembic migration) tracks every connector HTTP call — connector/action IDs, agent/conversation context, HTTP method/status, latency, request/response size; callback pattern in `ConnectorToolAdapter` with automatic logging in chat endpoints
- **Connector Stats API**: `GET /api/admin/connector-stats` endpoint returns per-connector call counts, success rates, average latency, and last-called timestamps for admin monitoring
- **Enhanced Admin Overview Stats**: Admin stats endpoint now includes KB document/chunk counts, total connector count, today's conversation count, and tokens-by-agent breakdown for the recharts dashboard
- **Admin Connectors Tab**: New "Connectors" tab in the admin panel showing connector call metrics (total calls, success rate, avg latency, last called) alongside the existing Overview and Users tabs
- **Tokens by Agent Chart**: Admin overview dashboard displays a tokens-by-agent bar chart for per-agent token consumption visibility
- **Fast LLM Token Tracking**: All fast LLM calls (compact, context guard, grounding, suggestions) now record token consumption via per-request `UsageTracker`; `fast_llm_tokens` column on Conversation model with SQLite auto-migration and Alembic migration; admin dashboard pie chart shows separate "Fast LLM" slice and Total Tokens card shows secondary "Fast LLM: X.XK" line

### Changed
- **Tool Display — Backend Source of Truth**: `Tool` protocol and `BaseTool` now expose `display_name`; builtin tools override where title-casing is wrong (`http_request` → "HTTP Request", `kb_list` → "KB List", `kb_retrieve` → "KB Retrieve", `python_exec` → "Python", `file_ops` → "File Operations", `grounded_retrieve` → "Grounded Retrieve"); MCP/Connector adapters produce `"{server}: {action}"` format; `ToolRegistry.to_catalog()` serialises all metadata; `GET /api/tools/catalog` (no auth) serves it to the frontend with 5-min SWR cache
- **Tool Catalog UI**: Built-in tools section reads from catalog API (zero hardcoding); cards are click-to-expand for descriptions; Connector/MCP link cards show display names; agent settings category tooltips list tool names dynamically; `getToolDisplayName()` / `getToolIcon()` in step-summary use catalog with fallback by naming convention
- **Navigation Links**: All navigation buttons replaced with Next.js `<Link>` components for proper middle-click/Cmd+Click new-tab behavior across agent cards, connector cards, KB cards, sidebar conversations, chats page, and all "New" buttons
- **Mode Labels**: "ReAct" renamed to "Standard", "DAG" renamed to "Planner" across sidebar, playground mode toggle, and examples; mode toggle now shows tooltip with description
- **Tools Page Redesign**: Split into Tabs (Built-in / MCP Servers); built-in tools section redesigned with card grid layout, per-tool icons with category colors, and category filter chips
- **Toast Feedback**: All CRUD operations now show sonner toast notifications (success/error) replacing silent `console.error()` — affects agents, connectors, KB, chunks, actions, and MCP server dialogs
- **Dirty State Protection**: MCP server dialog and agent editor page now warn before discarding unsaved changes (AlertDialog confirm on close/navigate); agent editor adds `beforeunload` browser warning
- **Setup Page**: Email field now required (was optional); client-side email format validation
- **Tool Category Tooltips**: Agent settings form shows tooltip on hover for each tool category with description and included tools list
- **Iteration Detail Drawer**: Tab bar and stats (summary, duration) combined into a single row for cleaner layout
- **Input/Textarea Focus**: Changed from ring-based to outline-based focus style for consistency
- **KB Detail Labels**: "Upload" → "Add Documents", "New MD" → "Write Note"
- **Agent Selector**: Shows agent emoji/icon instead of generic User icon; uses agent ID as value for correct selection with duplicate names
- **Retry Button**: Playground shows a retry button for stopped/interrupted tasks (both in-session and after page refresh)
- **Login Page Redesign**: Split layout with left brand panel (dark, mesh gradient, tagline) and right form panel (theme-aware); removed Satoshi font, kept Cabinet Grotesk
- **Sidebar Logo Font**: Increased from `text-sm` to `text-base`
- **Frontend API Proxy**: Migrated all frontend API calls to Next.js proxy rewrites (`getApiBaseUrl` for client-side proxy, `getApiDirectUrl` for server-side direct access); eliminates CORS issues and hides backend URL from browser
- **Roadmap v1.1 restructured**: Renamed from "Enterprise & Scale" to "Agent as a Service" — added Agent Publishing (published pages, API endpoints, API keys, version snapshots, custom branding), 3 delivery channels (Web App / API / Embed), access control (visitor modes, rate limiting, domain/IP restrictions), and usage analytics (per-agent dashboard, conversation logs, cost attribution); existing Connector Ecosystem and Enterprise Operations items retained
- Sidebar "New Chat" and "Search" restyled as minimal plain buttons with hover keyboard shortcut hints (⇧⌘O / ⌘K)
- Sidebar logo size reduced from h-6 to h-5
- Frontend i18n moved from v0.6.2 scope to v1.0 backlog; User Language Preference (backend) remains shipped
- Connectors now default to "published" on creation; removed publish/unpublish workflow and status badge
- Agent form always sends `kb_ids` and `connector_ids` (even when empty); confidence threshold moved above connectors section
- LanceDB auto-migrates missing schema columns on table open
- KB chunk drawer search replaced with collapsible toggle icon button (auto-focus, Escape to close)
- KB detail page "Search" tab renamed to "Retrieve Test"
- Inject recall button now always visible (not hidden on success)
- **ThinkingCard Loading**: Simplified to single "Processing..." label with shiny-text shimmer animation; removed fake rotating THINKING_HINTS
- **Monospace Font**: Switched to JetBrains Mono for `--font-mono` (replaces Inter)
- **Sidebar Layout**: Width w-60→w-72; logo clickable (links to /new); collapsed mode keeps only search button; "New chat" icon restyled with background container
- **Step Collapsible Groups**: DAG and ReAct step cards rendered inside the collapsible summary container (no longer floating below the toggle bar)
- **Result Drawer**: Step result display changed from inline collapsible to right-side Sheet drawer for full-width markdown rendering
- **History Step Cards**: Completed DAG turns in history hide individual step cards, showing only the summary bar
- **ThinkingCard Layout**: Icon moved from circular wrapper into inline Badge; removed excess left padding

### Fixed
- **DAG SSE Streaming**: EventSource now connects directly to backend, bypassing Next.js rewrite proxy that buffered SSE events (caused DAG progress to arrive in batches instead of frame-by-frame)
- **ReAct Interrupt Queue**: Added missing `await` to `InterruptQueue.drain()` calls in ReAct agent; without await, coroutine objects were returned instead of message lists, breaking mid-stream injection
- **DAG Early Stop**: Explicitly cancel all in-flight DAG step tasks when user hits Stop, preventing orphaned LLM calls and tool executions
- **Async Concurrency Hardening**: Added `asyncio.Lock` to `InterruptQueue` and `UsageSummary` aggregation; converted `DbMemory` to async-with sessions; re-raised `CancelledError` instead of suppressing; narrowed exception handling; bounded SSE queues to `maxsize=1000`; added `step_timeout` to `DAGExecutor`
- IME composition handling in chat input (Chinese/Japanese input no longer triggers premature submit)
- Playground scroll behavior and inject animation timing
- DAG skip button for running/pending steps during interrupt
- Remove excessive slide-in animations from playground, DAG, and step components
- Agent form custom checkbox styling and orphan resource display
- Resource ownership validation on agent binding and KB lookup
- Connector publish/unpublish confirmation dialogs (now removed)
- Evidence-utils citation regex matching for grounded retrieval
- Grounded retrieve tool fallback format when no evidence found
- Failed inject content now restored to input box with toast warning instead of being lost
- **Conversation model_name**: `model_name` now resolved eagerly at conversation creation time, eliminating "Unknown" entries in admin stats

## [0.6.1] — Connector Entity & Manual Builder

### Added
- **Connector + ConnectorAction ORM**: Database models for connectors and their actions
- **Connector CRUD API**: Full REST endpoints at `/api/connectors` and `/api/connectors/{id}/actions`
- **ConnectorToolAdapter**: Wraps ConnectorAction as BaseTool; HTTP proxy with path param substitution, auth injection (bearer with configurable prefix, API key with custom header, basic auth), default credential fallback from `auth_config`
- **Agent Connector Binding**: `connector_ids` field on Agent model; `_resolve_tools()` loads bound connectors and registers actions as tools
- **Connector Management UI**: List page, create/edit form, action editor (method, path, parameters schema)
- **Agent Form Connector Selector**: Multi-select connector binding in agent configuration (like KB binding)
- **Per-Auth-Type Config UI**: Bearer token prefix + default token, API key header name + default key, basic auth username/password

### Changed
- Database migration adds `connector` and `connector_action` tables, `connector_ids` column on agent

## [0.6.0] — Portal UX & Connector Platform Foundation

### Added
- **Dark / Light / System Theme**: `next-themes` provider with system-preference detection, manual toggle in Appearance settings, full component theming
- **Suggested Follow-ups**: Fast LLM generates 2-3 follow-up questions as clickable chips after agent completes
- **Conversational Interrupt**: Send messages while agent is running; injected at next iteration boundary; backend async queue per conversation; frontend inject mode with Stop button; messages persist as `message_type="inject"` for replay
- **Conversational Interrupt UI**: Inject event display, auto-focus, optimistic UI with SSE confirmation
- **MarkItDown Integration**: DOCX/XLSX/PPTX document loading via MarkItDown library
- **DAG Interrupt Skip**: Skip running/pending DAG steps during interrupt
- **Env-Driven Execution Limits**: Configurable `MAX_REACT_ITERATIONS` and `MAX_DAG_STEPS` via environment variables

### Changed
- Rebrand "Adapter" to "Connector", "Sidecar" to "Copilot" across codebase and docs
- Iteration card UI redesign: compact log-style cards with English summaries, tab switcher for Args/Obs, unified step/tool call display components

### Fixed
- Chats page: refetch conversation list after delete; add "All Chats" sidebar link
- Agent resolution hardening for edge cases

## [0.5.0] — RAG, Knowledge & Memory

### Added
- **LLM Compact**: Fast LLM compresses early conversation turns into a summary when history exceeds threshold; retain recent turns verbatim
- **DAG Re-Planning**: Auto re-plan when analyzer confidence < 0.5, up to 3 rounds; new SSE `replanning` phase event
- **ContextGuard**: Unified context window budget manager; hint-specific LLM compaction, oversized message truncation, smart fallback; wired into ReAct and DAG
- **Pinned Messages**: `ChatMessage.pinned` flag protects critical messages from compaction; three-way split (system / pinned / compactable); DAG steps receive `original_goal` context
- **DAG Multi-Turn Polish**: Structured message history for planner (replaces text prefix injection)
- **DAG LLM Compact**: Apply LLM compact to enriched planner query before planning
- **Tool-Name-Aware Planning**: Planner `tool_hint` constrained to available tool names
- **DAG Step-Level ReAct**: Each DAG step runs a full ReAct agent loop instead of single LLM call
- **DAG Step-Level Memory**: Step executors see relevant prior conversation context
- **DAG History Replay**: Frontend replays persisted DAG executions with flow graph
- **Embedding**: `BaseEmbedding` protocol + OpenAI-compatible implementation (Jina jina-embeddings-v3)
- **Vector Store**: LanceDB embedded vector store with native FTS, content-hash dedup
- **Document Loaders**: PDF, DOCX, Markdown, HTML, CSV, plain text
- **Chunking Strategies**: Fixed-size, recursive, and semantic chunking
- **Hybrid Retrieval**: Dense vector search + LanceDB native FTS + RRF fusion + Jina reranker
- **Knowledge Base Management**: Full KB CRUD with document upload, background ingest, agent `kb_retrieve` tool
- **Grounded Generation**: 5-stage evidence-anchored RAG pipeline — multi-KB retrieval, citation extraction, alignment scoring, conflict detection, confidence computation; inline `[N]` citation markers with References panel
- **Chunk-Level CRUD**: View, edit, delete individual chunks; inline chunk editor
- **KB Detail Page**: Dedicated page with document table and chunk browser
- **Markdown Document Creation**: Create markdown documents directly from KB UI
- **Citation References UI**: Structured references section with confidence badge, source names, page numbers, exact quotes
- **DAG Step Progress UI**: Visual progress indicator for DAG step execution
- **Per-User KB Isolation**: Knowledge bases scoped to the owning user
- **Conversation Starring & Pinning**: Star/pin conversations for quick access
- **Batch Delete Conversations**: Multi-select and delete conversations from sidebar
- **Chat Search (Cmd+K)**: Command palette for finding conversations by title or content
- **Conversation Rename**: Inline rename from sidebar
- **Global User Instructions**: Per-user system instructions injected into every agent conversation
- **Personal Center**: Settings page with user profile and preferences
- **Clipboard Image Paste**: Paste images from clipboard into chat input
- **Execution Mode Decoupling**: Separate agent execution mode from model configuration
- **Grounding Threshold**: Configurable confidence threshold for grounded generation

### Changed
- Sidebar UX overhaul with settings navigation
- Chunk size hardening and validation
- Analyzer retry logic for edge cases

### Fixed
- Format token counts as compact "k" notation in chat UI

## [0.4.0] — Platform Foundation

### Added
- **HTTP Request Tool**: General-purpose HTTP client with SSRF protection, JSON auto-pretty-print, 200KB response limit
- **Shell Exec Tool**: Sandboxed shell execution with command blocklist (30+ patterns), env var scrubbing, per-user sandbox directory
- **Persistent Storage**: SQLAlchemy ORM + SQLite with async WAL mode engine
- **Conversation Persistence**: Durable session history; `DbMemory` with smart truncation, CJK-aware token estimation, auto-compact
- **Multi-Tenant Auth**: User registration/login (JWT), SSE token-based auth, conversation ownership validation
- **Project & Agent Management**: Agent CRUD, tool/model/prompt binding per agent, agent-aware chat endpoints
- **File Upload & Management**: Upload/download/delete with per-user isolation
- **Drag-and-Drop File Upload**: Drop files onto chat input with visual overlay
- **Multi-Turn Conversation**: Full multi-turn context with smart truncation and thinking UX overhaul
- **SSE Abort/Disconnect**: Graceful SSE connection handling and client-initiated abort
- **DAG Horizontal Layout**: Horizontal orientation for DAG flow graph
- **Step Timing in Sidebar**: Elapsed duration display per step in sidebar

### Changed
- Token usage display moved to per-message in chat UI (backend tracking from v0.2)
- Sidebar resize behavior improvements

### Fixed
- Token count display formatted as compact "k" notation

## [0.3.0] — Rich Tool Ecosystem

### Added
- **Web Search Tool**: Jina-powered web search (`s.jina.ai`)
- **Web Fetch Tool**: Jina-powered HTTP reader (`r.jina.ai`) for clean page content extraction
- **DAG Visualization**: Interactive @xyflow/react flow graph with real-time step status, dependency edges, duration, iteration details
- **Right Sidebar Panel**: Persistent sidebar with expand/collapse toggle, auto fitView, DAG flow graph + ReAct timeline, click-to-scroll, responsive auto-hide
- **Env-Driven Configuration**: `LLM_TEMPERATURE` and `MAX_CONCURRENCY` via environment variables
- **Calculator Tool**: Safe AST-based math expression evaluation
- **File Ops Tool**: Sandboxed file read/write/list/mkdir with path traversal protection
- **Python Exec Sandbox**: Restricted builtins whitelist, blocked dangerous modules, stderr capture, 100KB output limit
- **Tool Auto-Discovery**: Convention-based loading — drop a `BaseTool` subclass in `builtin/` and it auto-registers
- **MCP Client**: Model Context Protocol integration via stdio transport; auto-discover and register external tools; configured via `MCP_SERVERS` env var
- **Tool Categories & Permissions**: Category tagging, `filter_by_category()`, `exclude_by_name()`, category-filtered `to_openai_tools()`
- **DAG Node Detail Enhancement**: Tool list, start time, and elapsed duration on each DAG step node
- **Frontend Token Usage**: Per-task input/output token counts in chat UI

### Fixed
- Parallel tool timing: correct elapsed-time measurement for concurrent tool calls in native function-calling mode
- ReAct agent JSON parsing reliability improvements

## [0.2.0] — Core Enhancements

### Added
- **Native Function Calling**: OpenAI-style `tool_choice` / `parallel_tool_calls` alongside ReAct JSON mode
- **Conversation Memory**: Short-term message window + conversation summary for multi-turn sessions
- **Retry & Rate Limiting**: Configurable retry with exponential backoff + token-bucket rate limiter
- **Token Usage Tracking**: Prompt/completion token counts across all LLM calls; per-task cost summary
- **Multi-Model Support**: ModelRegistry for multiple LLMs per project (general / fast / vision / compact)
- **Next.js Portal**: Interactive playground with FastAPI backend

## [0.1.0] — Foundation

### Added
- **ReAct Agent**: JSON-mode reasoning and action loop
- **DAG Planning**: DAGPlanner -> DAGExecutor -> PlanAnalyzer with concurrent step execution
- **Web UI Playground**: Interactive chat interface with DAG progress streaming
- **Real-Time Streaming**: Async queue bridge and `on_iteration` callbacks for both ReAct and DAG modes via SSE
- **KaTeX Math Rendering**: LaTeX math display in frontend
- **Step Timestamps**: Timing information on execution steps
- **Robust JSON Parsing**: Resilient parsing for LLM-generated JSON with fallback strategies

### Fixed
- Step dependency display improvements

### Fixed
- MCP stdio client: `RuntimeError: Attempted to exit cancel scope in a different task` — moved `connect_stdio()` from the request handler coroutine into the SSE generator body via `_connect_pending_mcp_servers()` helper, so anyio cancel scope enter/exit happen in the same coroutine

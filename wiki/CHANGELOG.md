# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions correspond to [Roadmap](Roadmap.md) milestones.

## [Unreleased]

### Added
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

### Changed
- Sidebar "New Chat" and "Search" restyled as minimal plain buttons with hover keyboard shortcut hints (⇧⌘O / ⌘K)
- Sidebar logo size reduced from h-6 to h-5
- Frontend i18n moved from v0.6.2 scope to v1.0 backlog; User Language Preference (backend) remains shipped
- Connectors now default to "published" on creation; removed publish/unpublish workflow and status badge
- Agent form always sends `kb_ids` and `connector_ids` (even when empty); confidence threshold moved above connectors section
- LanceDB auto-migrates missing schema columns on table open
- KB chunk drawer search replaced with collapsible toggle icon button (auto-focus, Escape to close)
- KB detail page "Search" tab renamed to "Retrieve Test"
- Inject recall button now always visible (not hidden on success)

### Fixed
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

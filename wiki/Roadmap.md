# Roadmap

## Roadmap

> Goal: Build a complete **Dify alternative** -- from agent runtime to visual workflow builder.

### v0.2 -- Core Enhancements

- [ ] **Native Function Calling**: Support OpenAI-style `tool_choice` / `parallel_tool_calls` alongside the ReAct JSON mode
- [x] **Streaming Agent Output**: ~~Yield intermediate reasoning and tool results as they happen via `AsyncIterator`~~ -- Shipped in v0.1: real-time SSE streaming with async queue bridge and `on_iteration` callbacks for both ReAct and DAG modes
- [ ] **Conversation Memory**: Short-term message window + long-term summary (LanceDB) for multi-turn agent sessions
- [ ] **Retry & Fallback**: Configurable retry policies for LLM calls and tool executions with exponential backoff
- [ ] **Multi-Model Support**: Configure multiple LLMs per project (general / fast / vision / compact), switch per step

### v0.3 -- Rich Tool Ecosystem

- [ ] **Built-in Tools**: Calculator, file ops, web search, browser automation, image understanding/generation
- [ ] **MCP Integration**: Model Context Protocol server management for dynamic tool discovery and invocation
- [ ] **Tool Categories & Permissions**: Group tools by category, enable/disable per agent
- [ ] **Skill System**: Reusable prompt-based skills that agents can compose

### v0.4 -- RAG & Knowledge

- [ ] **Vector Store Retrievers**: Built-in implementations for LanceDB, FAISS, Chroma, and Milvus
- [ ] **Hybrid Retrieval**: Combine dense vector search with BM25 sparse retrieval
- [ ] **Knowledge Base Management**: Create, upload, search, and manage multiple knowledge bases
- [ ] **Document Loaders**: File parsers for PDF, Markdown, HTML, DOCX, and CSV
- [ ] **Chunking Strategies**: Fixed-size, recursive, and semantic chunking out of the box

### v0.5 -- Agent Builder & Marketplace

- [ ] **Agent Builder**: Visual UI to create custom agents -- set instructions, pick model, attach knowledge bases, configure tools
- [ ] **Agent Publishing**: Draft / published lifecycle, share agents with teammates
- [ ] **Agent Templates**: Pre-built agent templates for common use cases (code assistant, research, data analysis)
- [ ] **Text2SQL Agent**: Specialized agent that connects to databases and generates SQL from natural language
- [ ] **Vibe Mode**: Alternative creative execution mode for open-ended exploration tasks

### v0.6 -- Multi-Agent Collaboration

- [ ] **Agent Roles**: Define specialized agents (researcher, coder, reviewer) within a single DAG plan
- [ ] **Inter-Agent Messaging**: Shared context bus for agents to exchange intermediate results
- [ ] **Human-in-the-Loop**: Approval gates and intervention points in DAG steps for high-stakes decisions
- [ ] **Agent Delegation**: Allow agents to spawn sub-agents for nested task decomposition
- [ ] **Task Pause / Resume**: Persist and resume long-running agent tasks

### v0.7 -- Production Platform

- [ ] **User Management**: Multi-user with JWT auth, role-based permissions, workspace isolation
- [ ] **Persistent Storage**: SQLAlchemy ORM with PostgreSQL / SQLite for tasks, agents, and execution history
- [ ] **File Workspace**: Per-task file management -- upload, download, preview, input/output directories
- [ ] **WebSocket Communication**: Real-time bi-directional streaming replacing SSE for richer interaction
- [ ] **DAG Visualization**: Interactive Dagre/XYFlow graph rendering of execution plans

### v0.8 -- Observability & Operations

- [ ] **OpenTelemetry Traces**: Structured tracing for every LLM call, tool execution, and DAG step
- [ ] **Token & Cost Tracking**: Token usage aggregation across planning, execution, and analysis rounds
- [ ] **Monitoring Dashboard**: Task history, execution metrics, performance analytics
- [ ] **Rate Limiting**: Token-aware rate limiter respecting provider quotas
- [ ] **Execution Replay**: Historical trace replay for debugging and auditing

### v0.9 -- Workflow Engine (Dify Parity)

- [ ] **Visual Workflow Editor**: Drag-and-drop node-based workflow builder (like Dify / n8n)
- [ ] **Workflow Templates**: Pre-built workflow templates for common patterns (chatbot, RAG pipeline, data processing)
- [ ] **Conditional Branching**: If/else nodes, switch nodes, loop nodes in visual workflows
- [ ] **Variable System**: Global and step-scoped variables with type validation
- [ ] **Trigger System**: HTTP webhook, scheduled cron, and event-based workflow triggers
- [ ] **Workflow Versioning**: Version control for published workflows with rollback support

### v1.0 -- Ecosystem & Enterprise

- [ ] **Plugin System**: Pip-installable tool / retriever / agent packages with auto-registration
- [ ] **Full Web UI**: Next.js dashboard with agent marketplace, task management, and workflow editor
- [ ] **Docker Deployment**: Production-ready Docker Compose with PostgreSQL, Redis, and worker processes
- [ ] **REST & SSE API**: Complete HTTP API for programmatic access to all platform features
- [ ] **Benchmarks**: Standardized evaluation suite against SWE-bench, HotpotQA, and custom enterprise tasks
- [ ] **i18n**: Multi-language support for UI and agent prompts (Chinese, English, Japanese)

Contributions and ideas are welcome -- open an issue or submit a PR on [GitHub](https://github.com/fim-ai/fim-agent).

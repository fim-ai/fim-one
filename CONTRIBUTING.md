# Contributing to FIM One

Thank you for your interest in FIM One! We value every form of contribution — especially bug reports, security reviews, and real-world usage feedback.

> **Pioneer Program**: The first 100 contributors are recognized as **Founding Contributors** with permanent credits in the project. [Details below](#-pioneer-program).

## Table of Contents

- [Pioneer Program](#-pioneer-program)
- [What We Need Most](#what-we-need-most)
- [Security Reports](#security-reports)
- [Field Testing Program](#-field-testing-program)
- [Bug Reports](#bug-reports)
- [Code Contributions](#code-contributions)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Coding Conventions](#coding-conventions)
- [Submitting Changes](#submitting-changes)
- [Community](#community)

## 🏆 Pioneer Program

We believe early contributors deserve lasting recognition. The **Pioneer Program** rewards the first 100 contributors:

| Tier                     | Who                    | Perks                                                                                                     |
| ------------------------ | ---------------------- | --------------------------------------------------------------------------------------------------------- |
| **Founding Contributor** | First 100 contributors | Permanent avatar in README, `founding-contributor` GitHub badge, name in CREDITS, priority issue response |
| **Early Adopter**        | Contributors #101–500  | Avatar in README, `early-adopter` badge                                                                   |

**What counts as a contribution?**

- A merged PR (bug fix, docs, translation, code)
- A quality bug report with clear reproduction steps
- A security vulnerability report (via responsible disclosure)
- A detailed field test report sharing your real-world use case

We use the [all-contributors](https://allcontributors.org/) specification. After your contribution is accepted, comment on the issue/PR:

```
@all-contributors please add @<your-username> for <contribution-type>
```

Contribution types: `bug`, `security`, `test`, `code`, `doc`, `translation`, `ideas`, `userTesting`

## What We Need Most

This is a solo-developer project. Here's what helps the most, in priority order:

### 1. Bug Hunting (Highest Impact)

Deploy FIM One, use it in your workflow, and report what breaks. We especially need:

- **Edge cases in agent reasoning** — queries where the ReAct agent loops, gives wrong tool calls, or misinterprets intent
- **DAG planning failures** — tasks where the planner generates bad dependency graphs or re-plans unnecessarily
- **Connector issues** — API auth failures, response parsing errors, timeout edge cases
- **Frontend bugs** — UI glitches, broken interactions, i18n issues, accessibility problems
- **Concurrency bugs** — race conditions under concurrent users, streaming SSE issues

### 2. Security Review

See [Security Reports](#security-reports) below. We take security seriously and credit all reporters.

### 3. Real-World Usage Feedback

We want to understand how people actually use FIM One. See [Field Testing Program](#-field-testing-program).

### 4. Code Contributions

Bug fixes and improvements are welcome. New features are accepted too — we'll review carefully and merge if they align with the project direction. Please open an issue first to discuss before building anything large.

## Security Reports

**Do NOT open public issues for security vulnerabilities.**

If you discover a security issue, please report it responsibly:

1. **Email**: security@fim.ai (preferred)
2. **GitHub**: Use [Security Advisories](https://github.com/fim-ai/fim-one/security/advisories/new) (private)

What we're looking for:

| Area               | Examples                                                                            |
| ------------------ | ----------------------------------------------------------------------------------- |
| **Injection**      | Prompt injection via tool outputs, SQL injection in search, XSS in rendered content |
| **Auth & Access**  | JWT bypass, privilege escalation, IDOR in API endpoints                             |
| **Code Execution** | Sandbox escape in `python_exec`, path traversal in file tools                       |
| **Data Exposure**  | API keys leaked in logs, user data in error messages, connector credentials exposed |
| **Dependency**     | Known CVEs in dependencies, supply chain risks                                      |

**Response timeline:**

- Acknowledgment: within 48 hours
- Assessment: within 7 days
- Fix: as fast as possible, coordinated disclosure

**Recognition**: All confirmed security reporters are credited in the [Security Hall of Fame](#security-hall-of-fame) (unless they prefer anonymity) and count toward the Pioneer Program.

### Security Hall of Fame

*Be the first security researcher credited here.*

## 🧪 Field Testing Program

We need real-world feedback more than code. If you deploy FIM One in any environment — personal, team, or enterprise — your experience is incredibly valuable.

### How to Participate

1. **Deploy** FIM One (Docker or local)
2. **Use it** for your actual tasks — don't just test the demo
3. **Open a GitHub Issue** with the `field-test` label using this template:

```markdown
### Environment
- Deployment: Docker / Local
- LLM Provider: OpenAI / DeepSeek / Ollama / ...
- Scale: solo / team (N users) / enterprise

### Use Case
What are you trying to accomplish? What systems are you connecting?

### What Worked
Things that went smoothly.

### What Didn't Work
Bugs, confusing UX, wrong agent behavior, missing features.

### Edge Cases Found
Queries or scenarios that produced unexpected results.

### Suggestions
What would make this more useful for your workflow?
```

**Why this matters**: Understanding real-world tasks, boundaries, and failure modes is more valuable than any feature PR. Your field test report directly shapes the roadmap.

## Bug Reports

A good bug report is worth its weight in gold. Please include:

- **Environment**: OS, Python version, Node version, LLM provider
- **Steps to reproduce**: minimal, specific, and numbered
- **Expected vs actual behavior**: what should happen vs what did happen
- **Logs or screenshots**: error messages, browser console, server logs
- **Severity estimate**: crash / data loss / wrong result / cosmetic

Bonus points:
- Include the agent's reasoning trace (visible in the UI thinking panel)
- Include the DAG visualization screenshot for planning issues
- Note the LLM model used — different models produce different failure modes

## Code Contributions

### What's Welcome

| Type                 | Examples                                            |
| -------------------- | --------------------------------------------------- |
| **Bug fixes**        | Fix UI glitch, resolve API error, correct edge case |
| **Security patches** | Fix vulnerabilities, harden inputs                  |
| **Test coverage**    | Add tests for untested code paths                   |
| **Documentation**    | Improve guides, fix typos, add examples             |
| **Translations**     | Improve i18n strings (EN/ZH)                        |
| **Performance**      | Optimize streaming, reduce latency                  |
| **New features**     | Welcome — open an issue to discuss first            |

### What to Discuss First

Open an issue before starting work on:

- New built-in tools or connectors
- Changes to the core agent loop or DAG planner
- New UI pages or major component changes
- Architectural changes

This avoids wasted effort if the direction doesn't align.

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 20+ with [pnpm](https://pnpm.io/)
- Git

### Backend

```bash
# Install all dependencies (--all-extras is required!)
uv sync --all-extras

# Run tests
uv run pytest

# Run linter
uv run ruff check src/ tests/

# Run type checker
uv run mypy src/

# Start dev server with hot reload
./start.sh dev
```

### Frontend

```bash
cd frontend

# Install dependencies
pnpm install

# Start dev server
pnpm dev

# Run linter
pnpm lint

# Production build (must pass before submitting PR)
pnpm build
```

### Environment

```bash
cp example.env .env
# Edit .env with your API keys (at minimum: LLM_API_KEY, LLM_MODEL)
```

## Project Structure

```
src/fim_one/
├── core/
│   ├── agent/       # ReAct agent (reasoning + action loop)
│   ├── model/       # LLM abstraction (provider-agnostic)
│   ├── planner/     # DAG planner → executor → analyzer
│   ├── memory/      # Conversation memory (window, summary, DB)
│   └── tools/       # Tool base classes + connector adapter
├── web/             # FastAPI backend (REST API)
├── rag/             # RAG pipeline (retrieval, grounding)
├── db/              # Database models (SQLAlchemy)
└── migrations/      # Alembic database migrations

frontend/            # Next.js portal (shadcn/ui)
├── src/app/         # App Router pages
├── src/components/  # React components
├── src/lib/         # API clients, utilities
└── messages/        # i18n strings (en/ + zh/)

tests/               # pytest test suite
docs/                # Mintlify documentation
```

## Coding Conventions

### Python (Backend)

- **Type hints** on all public functions
- **Async-first**: use `async def` for I/O-bound operations
- **Linter**: Ruff (line length 100, rules: E, F, I, N, UP, B, SIM, RUF)
- **Tests**: every new module should have a corresponding `tests/test_*.py`
- **Imports**: keep `__init__.py` imports minimal — only re-export public API

### TypeScript (Frontend)

- **i18n is mandatory**: all UI text must use `next-intl`, never hardcode strings
  - Add keys to both `messages/en/{ns}.json` and `messages/zh/{ns}.json`
- **No native dialogs**: use shadcn `AlertDialog` / `Dialog` / Toast (sonner)
- **Navigation**: use `<Link>`, not `<button onClick={router.push()}>`
- **Admin tables**: row actions must use a "..." `DropdownMenu` (see `admin-users.tsx`)
- **Error handling**: inline for field errors, `toast.error()` for system errors

### General

- Keep changes focused — one concern per PR
- No over-engineering: minimum complexity for the current task
- Don't add docstrings/comments to code you didn't change
- Prefer editing existing files over creating new ones

## Submitting Changes

### Branch Naming

```
fix/issue-number          # Bug fix
security/what-fixed       # Security patch
docs/what-changed         # Documentation
feat/short-description    # New feature
refactor/what-changed     # Code refactoring
```

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
fix: resolve DAG re-planning infinite loop (#123)
security: sanitize user input in connector proxy
docs: update connector development guide
feat: add Slack connector with OAuth2 support
```

### Pull Request Checklist

Before submitting, ensure:

- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pytest` passes
- [ ] `cd frontend && pnpm build` passes (if frontend changes)
- [ ] i18n strings added to both `en/` and `zh/` (if UI text changed)
- [ ] New features have corresponding tests
- [ ] PR description explains **what** and **why**

### PR Process

1. Create a feature branch from `master`
2. Make your changes with atomic commits
3. Push to your fork and open a PR against `fim-ai/fim-one:master`
4. Fill in the PR template
5. Wait for review — maintainers aim to respond within 48 hours

## Community

- [Discord](https://discord.gg/z64czxdC7z) — chat with the maintainer and other users
- [GitHub Issues](https://github.com/fim-ai/fim-one/issues) — bugs, security, and field test reports
- [GitHub Discussions](https://github.com/fim-ai/fim-one/discussions) — questions, ideas, and use case sharing
- [Twitter / X](https://x.com/fim_one) — announcements and updates
- [Documentation](https://docs.fim.ai) — guides and API reference

## License

By contributing, you agree that your contributions will be licensed under the [FIM One Source Available License](LICENSE). This is not an OSI-approved open source license — please review it before contributing.

---

Thank you for helping build FIM One! Bug reports, security reviews, and field test stories are just as valuable as code. Every contribution counts.

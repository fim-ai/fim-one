<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![CI](https://github.com/fim-ai/fim-one/actions/workflows/test.yml/badge.svg)](https://github.com/fim-ai/fim-one/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**Der KI-Konnektorhub für globale Teams mit komplexen Enterprise-Stacks. Einbettbar als Copilot oder verbinde jedes System als Hub — einschließlich derjenigen, die deine globale Plattform nicht erreichen kann.**

🌐 [Website](https://one.fim.ai/) · 📖 [Docs](https://docs.fim.ai) · 📋 [Changelog](https://docs.fim.ai/changelog) · 🐛 [Bug melden](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ Überspringe das Setup — probiere FIM One in der Cloud.**
> Eine verwaltete Version ist live unter **[cloud.fim.ai](https://cloud.fim.ai/)**: kein Docker, keine API-Schlüssel, keine Konfiguration. Melde dich an und beginne, deine Systeme in Sekunden zu verbinden. _Early Access, Feedback willkommen._

---

## Übersicht

Große Unternehmen betreiben eine Vielzahl von Systemen, die nicht miteinander kommunizieren — ERP, CRM, OA, HR, Finanzsysteme, Datenbanken, IM-Plattformen in verschiedenen Regionen. FIM One ist der **AI-Connector-Hub**, der alle diese Systeme orchestriert, einschließlich der nur in China verfügbaren Systeme (Feishu, WeCom, DingTalk, DM, Kingbase usw.), die Ihre globale AI-Plattform nicht erreichen kann.

| Modus           | Beschreibung                                              | Zugriff                  |
| -------------- | ------------------------------------------------------- | ----------------------- |
| **Standalone** | Universeller AI-Assistent — Suche, Code, Wissensdatenbank         | Portal                  |
| **Copilot**    | AI eingebettet in der Benutzeroberfläche eines Host-Systems                       | iframe / Widget / Embed |
| **Hub**        | Zentrale AI-Orchestrierung über alle verbundenen Systeme   | Portal / API            |

```mermaid
graph LR
    ERP <--> Hub["🔗 FIM One Hub"]
    Database <--> Hub
    Lark <--> Hub
    Hub <--> CRM
    Hub <--> OA
    Hub <--> API[Custom API]
```

### Screenshots

**Dashboard** — Statistiken, Aktivitätstrends, Token-Nutzung und schneller Zugriff auf Agenten und Gespräche.

![Dashboard](./assets/screenshot-dashboard.png)

**Agent Chat** — ReAct-Reasoning mit mehrstufigen Tool-Aufrufen gegen eine verbundene Datenbank.

![Agent Chat](./assets/screenshot-agent-chat.png)

**DAG Planner** — LLM-generierter Ausführungsplan mit parallelen Schritten und Live-Statusverfolgung.

![DAG Planner](./assets/screenshot-dag-planner.png)

### Demo

**Agenten verwenden**

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

**Planer-Modus verwenden**

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## Schnelleinstieg

### Docker (empfohlen)

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

docker compose up --build -d
```

Öffnen Sie http://localhost:3000 — beim ersten Start erstellen Sie ein Admin-Konto. Das war's.

```bash
docker compose up -d          # start
docker compose down           # stop
docker compose logs -f        # view logs
```

### Lokale Entwicklung

Voraussetzungen: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, pnpm.

```bash
git clone https://github.com/fim-ai/fim-one.git && cd fim-one

cp example.env .env           # Edit: set LLM_API_KEY

uv sync --all-extras
cd frontend && pnpm install && cd ..

./start.sh dev                # hot reload: Python --reload + Next.js HMR
```

| Befehl          | Was wird gestartet                       | URL                            |
| ---------------- | --------------------------------- | ------------------------------ |
| `./start.sh`         | Next.js + FastAPI                 | localhost:3000 (UI) + :8000    |
| `./start.sh dev`     | Dasselbe, mit Hot Reload             | Dasselbe                           |
| `./start.sh dev:api` | Nur API, Entwicklungsmodus (Hot Reload)   | localhost:8000                 |
| `./start.sh dev:ui`  | Nur Frontend, Entwicklungsmodus (HMR)    | localhost:3000                 |
| `./start.sh api`     | FastAPI nur (headless)           | localhost:8000/api             |

> Für die Produktionsbereitstellung (Docker, Reverse Proxy, unterbrechungsfreie Updates) siehe das [Deployment-Handbuch](https://docs.fim.ai/quickstart#production-deployment).

## Hauptfunktionen

#### Connector Hub
- **Drei Bereitstellungsmodi** — Eigenständiger Assistent, eingebetteter Copilot oder zentraler Hub; gleicher Agent-Kern.
- **Jedes System, ein Muster** — Verbinden Sie APIs, Datenbanken, MCP-Server. Aktionen werden automatisch als Agent-Tools mit Auth-Injection registriert. Progressive Disclosure Meta-Tools reduzieren die Token-Nutzung um 80%+ über alle Tool-Typen hinweg.
- **Datenbank-Connectoren** — PostgreSQL, MySQL, Oracle, SQL Server und in China verbreitete Enterprise-Datenbanken (DM, KingbaseES, GBase, Highgo), die die meisten globalen Plattformen nicht erreichen können. Schema-Introspection und KI-gestützte Annotation.
- **Drei Möglichkeiten zum Erstellen** — OpenAPI-Spezifikation importieren, KI-Chat-Builder verwenden oder MCP-Server direkt verbinden.

#### Planung & Ausführung
- **Dynamische DAG-Planung** — LLM zerlegt Ziele zur Laufzeit in Abhängigkeitsgraphen. Keine hartcodierten Workflows.
- **Parallele Ausführung** — Unabhängige Schritte laufen parallel über asyncio; automatische Neuplanung bis zu 3 Runden.
- **ReAct-Agent** — Strukturierte Reasoning- und Acting-Schleife mit automatischer Fehlerwiederherstellung.
- **Agent-Harness** — Produktionsreife Ausführungsumgebung: ContextGuard für 5-schichtige Token-Budget-Verwaltung, progressive-disclosure Meta-Tools zur Beherrschung der Tool-Oberfläche und Self-Reflection-Schleifen zur Bekämpfung von Zieldrift.
- **Hook-System** — Deterministische Durchsetzung, die außerhalb der LLM-Schleife läuft. Erste Implementierung: `FeishuGateHook` sperrt sensitive Tool-Aufrufe hinter einer menschlichen Genehmigungskarte, die in eine Feishu-Gruppe gepostet wird. Erweiterbar auf Audit-Logging, Read-Only-Mode-Guards und Rate Limits (v0.9).
- **Auto-Routing** — Klassifiziert Anfragen und leitet zum optimalen Modus weiter (ReAct oder DAG). Konfigurierbar über `AUTO_ROUTING`.
- **Extended Thinking** — Chain-of-Thought für OpenAI o-Serie, Gemini 2.5+, Claude.

#### Workflow & Tools
- **Visual workflow editor** — 12 Knotentypen, Drag-and-Drop-Canvas (React Flow v12), Import/Export als JSON.
- **Smart file handling** — Hochgeladene Dateien werden automatisch in den Kontext eingefügt (klein) oder bei Bedarf über das `read_uploaded_file` Tool lesbar gemacht. Intelligente Dokumentverarbeitung: PDF-, DOCX- und PPTX-Dateien erhalten Vision-aware-Verarbeitung mit Extraktion eingebetteter Bilder, wenn das Modell Vision unterstützt. Der Smart-PDF-Modus extrahiert Text aus textreichen Seiten und rendert gescannte Seiten als Bilder.
- **Pluggable tools** — Python, Node.js, Shell-Ausführung mit optionalem Docker-Sandbox (`CODE_EXEC_BACKEND=docker`).
- **Full RAG pipeline** — Jina-Embedding + LanceDB + Hybrid-Retrieval + Reranker + Inline-`[N]`-Zitate.
- **Tool artifacts** — Rich Outputs (HTML-Vorschau, Dateien) im Chat gerendert.

#### Messaging Channels (v0.8)
- **Org-scoped IM bridge** — `BaseChannel` Abstraktion für ausgehende Nachrichten über Slack, Microsoft Teams, Discord, Feishu (Lark), WeCom und DingTalk. Erste Implementierung wird mit Feishu ausgeliefert; Slack / Teams / WeCom / Email stehen als nächstes auf der v0.9 Roadmap.
- **Fernet-verschlüsselte Anmeldedaten** — App-Secrets und Verschlüsselungsschlüssel sind im Ruhezustand verschlüsselt; jede eingehende Callback-Signatur wird verifiziert.
- **Interaktive Genehmigungskarten** — Channel-native `GateHook` (heute Feishu, nächstes Slack/Teams) postet eine Approve / Reject Karte in Ihre Gruppe, wenn ein sensitiver Tool-Aufruf ausgelöst wird; das Tool blockiert, bis ein Gruppenmitglied ein Urteil bestätigt. Human-in-the-Loop-Genehmigung ohne benutzerdefinierten Workflow-Engine.
- **Browse-and-pick UI** — Keine Notwendigkeit, rohe Channel-IDs aus der Vendor-Konsole zu kopieren; das Portal ruft die API der IM-Plattform auf und zeigt einen Gruppenwähler.

#### Plattform
- **Multi-Mandanten** — JWT-Authentifizierung, Organisationsisolation, Admin-Panel mit Nutzungsanalytics und Connector-Metriken.
- **Marketplace** — Veröffentlichen und abonnieren Sie Agenten, Connectors, KBs, Skills und Workflows.
- **Globale Skills (SOPs)** — Wiederverwendbare Betriebsverfahren, die für jeden Benutzer geladen werden; progressiver Modus reduziert Tokens um ~80%.
- **6 Sprachen** — EN, ZH, JA, KO, DE, FR. Übersetzungen sind [vollständig automatisiert](https://docs.fim.ai/quickstart#internationalization).
- **Assistent für die erste Einrichtung**, dunkles/helles Design, Befehlspalette, Streaming SSE, DAG-Visualisierung.

> Tiefergehende Informationen: [Architektur](https://docs.fim.ai/architecture/system-overview) · [Hook-System](https://docs.fim.ai/architecture/hook-system) · [Kanäle](https://docs.fim.ai/configuration/channels/overview) · [Ausführungsmodi](https://docs.fim.ai/concepts/execution-modes) · [Warum FIM One](https://docs.fim.ai/why) · [Wettbewerbslandschaft](https://docs.fim.ai/strategy/competitive-landscape)

## Architektur

```mermaid
graph TB
    subgraph app["Application Layer"]
        a["Portal · API · iframe · Feishu · Slack · WeCom · DingTalk · Teams · Email · Contract Systems · Custom Webhooks"]
    end
    subgraph mid["FIM One"]
        direction LR
        m1["Connectors<br/>+ MCP Hub"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m5["Hook System<br/>+ Channels"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems"]
        b["ERP · CRM · OA · Finance · Databases · Contract Mgmt · Custom APIs"]
    end
    app --> mid --> biz
```

Jeder Connector und jeder Channel ist eine standardisierte Brücke — der Agent weiß nicht und kümmert sich nicht darum, ob er mit SAP, einem benutzerdefinierten Vertragssystem oder einer Feishu-Gruppe spricht. Das Hook-System führt Plattformcode außerhalb der LLM-Schleife für Genehmigungen, Audits und Rate Limits aus; Channels übermitteln ausgehende Benachrichtigungen und Genehmigungskarten an externe IM-Plattformen. Weitere Informationen finden Sie unter [Connector-Architektur](https://docs.fim.ai/architecture/connector-architecture), [Hook-System](https://docs.fim.ai/architecture/hook-system) und [Channels](https://docs.fim.ai/configuration/channels/overview).

## Konfiguration

FIM One funktioniert mit **jedem OpenAI-kompatiblen Anbieter**:

| Anbieter           | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | -------------------- |
| **OpenAI**         | `sk-...`      | *(Standard)*                   | `gpt-4o`             |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`      |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6`  |
| **Ollama** (lokal) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`        |

Minimale `.env`:

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default
# LLM_MODEL=gpt-4o                         # default
JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

> Vollständige Referenz: [Umgebungsvariablen](https://docs.fim.ai/configuration/environment-variables)

## Tech Stack

| Layer       | Technology                                                          |
| ----------- | ------------------------------------------------------------------- |
| Backend     | Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio                 |
| Frontend    | Next.js 14, React 18, Tailwind CSS, shadcn/ui, React Flow v12      |
| AI / RAG    | OpenAI-kompatible LLMs, Jina AI (embed + search), LanceDB          |
| Database    | SQLite (dev) / PostgreSQL (prod)                                    |
| Messaging   | `BaseChannel` Abstraktion (Slack, Teams, Discord, Feishu/Lark, WeCom, DingTalk), Fernet-verschlüsselte Anmeldedaten, HMAC-Signaturverifizierung |
| Infra       | Docker, uv, pnpm, SSE Streaming                                    |

## Entwicklung

```bash
uv sync --all-extras          # install dependencies
pytest                         # run tests
pytest --cov=fim_one           # with coverage
ruff check src/ tests/         # lint
mypy src/                      # type check
bash scripts/setup-hooks.sh    # install git hooks (enables auto i18n)
```

## Roadmap

Siehe die vollständige [Roadmap](https://docs.fim.ai/roadmap) für Versionshistorie und geplante Funktionen.

## Häufig gestellte Fragen

Häufig gestellte Fragen zu Bereitstellung, LLM-Anbietern, Systemanforderungen und mehr – siehe [Häufig gestellte Fragen](https://docs.fim.ai/faq).

## Beitragen

Wir freuen uns über Beiträge aller Art — Code, Dokumentation, Übersetzungen, Fehlerberichte und Ideen.

> **Pioneer Program**: Die ersten 100 Mitwirkenden, deren PR zusammengeführt wird, werden als **Gründungsbeiträger** mit permanenten Credits, einem Badge und prioritärer Issue-Unterstützung anerkannt. [Mehr erfahren &rarr;](CONTRIBUTING.md#-pioneer-program)

**Schnelllinks:**

- [**Beitragsleitfaden**](CONTRIBUTING.md) — Setup, Konventionen, PR-Prozess
- [**Entwicklungskonventionen**](https://docs.fim.ai/contributing) — Typsicherheit, Tests und Code-Qualitätsstandards
- [**Good First Issues**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — kuratiert für Anfänger
- [**Offene Issues**](https://github.com/fim-ai/fim-one/issues) — Fehler & Feature-Anfragen

**Sicherheit:** Um eine Sicherheitslücke zu melden, öffnen Sie bitte ein [GitHub Issue](https://github.com/fim-ai/fim-one/issues) mit dem Tag `[SECURITY]`. Für vertrauliche Meldungen kontaktieren Sie uns über Discord DM.

## Stern-Verlauf

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>

## Aktivität

![Alt](https://repobeats.axiom.co/api/embed/49402c7d85e343e9cb5909da7b48db1930c76554.svg "Repobeats analytics image")

## Mitwirkende

Danke an diese wunderbaren Menschen ([Emoji-Schlüssel](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/tao-hpu"><img src="https://avatars.githubusercontent.com/u/1250043?v=4?s=80" width="80px;" alt="Tao An"/><br /><sub><b>Tao An</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Code">💻</a> <a href="#maintenance-tao-hpu" title="Maintenance">🚧</a> <a href="#design-tao-hpu" title="Design">🎨</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Documentation">📖</a> <a href="#projectManagement-tao-hpu" title="Project Management">📆</a> <a href="#ideas-tao-hpu" title="Ideas, Planning, & Feedback">🤔</a> <a href="#infra-tao-hpu" title="Infrastructure">🚇</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/tgonzalezc5"><img src="https://avatars.githubusercontent.com/u/102870299?v=4?s=80" width="80px;" alt="Teo Gonzalez Collazo"/><br /><sub><b>Teo Gonzalez Collazo</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Code">💻</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Tests">⚠️</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

Dieses Projekt folgt der [all-contributors](https://allcontributors.org/)-Spezifikation. Beiträge jeglicher Art sind willkommen!

## Lizenz

FIM One Source Available License. Dies ist **keine** von der OSI genehmigte Open-Source-Lizenz.

**Erlaubt**: interne Nutzung, Änderung, Verteilung mit intakter Lizenz, Einbettung in nicht konkurrierende Anwendungen.

**Eingeschränkt**: Multi-Tenant-SaaS, konkurrierende Agent-Plattformen, White-Labeling, Entfernung von Branding.

Für Anfragen zur kommerziellen Lizenzierung öffnen Sie bitte ein Issue auf [GitHub](https://github.com/fim-ai/fim-one).

Siehe [LICENSE](LICENSE) für vollständige Bedingungen.

---

<div align="center">

🌐 [Website](https://one.fim.ai/) · 📖 [Docs](https://docs.fim.ai) · 📋 [Changelog](https://docs.fim.ai/changelog) · 🐛 [Bug melden](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

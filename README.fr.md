<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![CI](https://github.com/fim-ai/fim-one/actions/workflows/test.yml/badge.svg)](https://github.com/fim-ai/fim-one/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**Le hub de connecteurs IA pour les équipes mondiales avec des stacks d'entreprise complexes. Intégrez en tant que Copilot, ou connectez chaque système en tant que Hub — y compris ceux que votre plateforme mondiale ne peut pas atteindre.**

🌐 [Site Web](https://one.fim.ai/) · 📖 [Documentation](https://docs.fim.ai) · 📋 [Journal des modifications](https://docs.fim.ai/changelog) · 🐛 [Signaler un bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ Ignorez la configuration — essayez FIM One sur Cloud.**
> Une version gérée est en direct sur **[cloud.fim.ai](https://cloud.fim.ai/)** : pas de Docker, pas de clés API, pas de configuration. Connectez-vous et commencez à connecter vos systèmes en quelques secondes. _Accès anticipé, les commentaires sont les bienvenus._

---

## Aperçu

Les grandes entreprises exploitent une multitude de systèmes qui ne communiquent pas entre eux — ERP, CRM, OA, HR, finance, bases de données, plateformes de messagerie instantanée dans différentes régions. FIM One est le **hub de connecteurs AI** qui orchestre l'ensemble de ces systèmes, y compris les systèmes exclusifs à la Chine (Feishu, WeCom, DingTalk, DM, Kingbase, etc.) que votre plateforme AI mondiale ne peut pas atteindre.

| Mode           | Description                                             | Accès                   |
| -------------- | ------------------------------------------------------- | ----------------------- |
| **Standalone** | Assistant AI polyvalent — recherche, code, KB           | Portail                 |
| **Copilot**    | AI intégré dans l'interface utilisateur d'un système    | iframe / widget / embed |
| **Hub**        | Orchestration AI centralisée sur tous les systèmes connectés | Portail / API           |

```mermaid
graph LR
    ERP <--> Hub["🔗 FIM One Hub"]
    Database <--> Hub
    Lark <--> Hub
    Hub <--> CRM
    Hub <--> OA
    Hub <--> API[Custom API]
```

### Captures d'écran

**Tableau de bord** — statistiques, tendances d'activité, utilisation des jetons et accès rapide aux agents et conversations.

![Dashboard](./assets/screenshot-dashboard.png)

**Chat d'agent** — raisonnement ReAct avec appels d'outils multi-étapes contre une base de données connectée.

![Agent Chat](./assets/screenshot-agent-chat.png)

**Planificateur DAG** — plan d'exécution généré par LLM avec étapes parallèles et suivi du statut en direct.

![DAG Planner](./assets/screenshot-dag-planner.png)

### Démo

**Utilisation d'agents**

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

**Utilisation du mode Planificateur**

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## Démarrage rapide

### Docker (recommandé)

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

docker compose up --build -d
```

Ouvrez http://localhost:3000 — au premier lancement, vous créerez un compte administrateur. C'est tout.

```bash
docker compose up -d          # start
docker compose down           # stop
docker compose logs -f        # view logs
```

### Développement local

Prérequis : Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 18+, pnpm.

```bash
git clone https://github.com/fim-ai/fim-one.git && cd fim-one

cp example.env .env           # Edit: set LLM_API_KEY

uv sync --all-extras
cd frontend && pnpm install && cd ..

./start.sh dev                # hot reload: Python --reload + Next.js HMR
```

| Commande         | Ce qui démarre                    | URL                            |
| ---------------- | --------------------------------- | ------------------------------ |
| `./start.sh`         | Next.js + FastAPI                 | localhost:3000 (UI) + :8000    |
| `./start.sh dev`     | Identique, avec rechargement à chaud | Identique                      |
| `./start.sh dev:api` | API uniquement, mode dev (rechargement à chaud) | localhost:8000                 |
| `./start.sh dev:ui`  | Frontend uniquement, mode dev (HMR) | localhost:3000                 |
| `./start.sh api`     | FastAPI uniquement (sans interface) | localhost:8000/api             |

> Pour le déploiement en production (Docker, proxy inverse, mises à jour sans interruption), consultez le [Guide de déploiement](https://docs.fim.ai/quickstart#production-deployment).

## Fonctionnalités principales

#### Hub de connecteurs
- **Trois modes de livraison** — Assistant autonome, Copilot intégré ou Hub central ; même noyau d'agent.
- **N'importe quel système, un seul modèle** — Connectez des API, des bases de données, des serveurs MCP. Les actions s'enregistrent automatiquement en tant qu'outils d'agent avec injection d'authentification. Les méta-outils à divulgation progressive réduisent l'utilisation de tokens de 80 %+ sur tous les types d'outils.
- **Connecteurs de bases de données** — PostgreSQL, MySQL, Oracle, SQL Server et bases de données d'entreprise courantes en Chine (DM, KingbaseES, GBase, Highgo) que la plupart des plateformes mondiales ne peuvent pas atteindre. Introspection de schéma et annotation alimentée par l'IA.
- **Trois façons de construire** — Importez une spécification OpenAPI, utilisez le générateur de chat IA ou connectez directement des serveurs MCP.

#### Planification et exécution
- **Planification DAG dynamique** — L'LLM décompose les objectifs en graphes de dépendances à l'exécution. Aucun workflow codé en dur.
- **Exécution concurrente** — Les étapes indépendantes s'exécutent en parallèle via asyncio ; re-planification automatique jusqu'à 3 tours.
- **Agent ReAct** — Boucle structurée de raisonnement et d'action avec récupération automatique des erreurs.
- **Harnais d'agent** — Environnement d'exécution de qualité production : ContextGuard pour la gestion des budgets de tokens à 5 niveaux, méta-outils à divulgation progressive pour maintenir la surface d'outils tractable, et boucles d'auto-réflexion pour contrer la dérive d'objectifs.
- **Système de hooks** — Application déterministe qui s'exécute en dehors de la boucle LLM. Premier livré : `FeishuGateHook` contrôle les appels d'outils sensibles derrière une carte d'approbation humaine affichée dans un groupe Feishu. Extensible à la journalisation d'audit, aux garde-fous en mode lecture seule et aux limites de débit (v0.9).
- **Routage automatique** — Classe les requêtes et les achemine vers le mode optimal (ReAct ou DAG). Configurable via `AUTO_ROUTING`.
- **Pensée étendue** — Chaîne de pensée pour OpenAI o-series, Gemini 2.5+, Claude.

#### Flux de travail et outils
- **Éditeur de flux de travail visuel** — 12 types de nœuds, canevas glisser-déposer (React Flow v12), import/export en JSON.
- **Gestion intelligente des fichiers** — Les fichiers téléchargés sont automatiquement intégrés au contexte (petits) ou lisibles à la demande via l'outil `read_uploaded_file`. Traitement intelligent des documents : les fichiers PDF, DOCX et PPTX bénéficient d'un traitement compatible avec la vision avec extraction d'images intégrées lorsque le modèle supporte la vision. Le mode PDF intelligent extrait le texte des pages riches en texte et affiche les pages numérisées sous forme d'images.
- **Outils enfichables** — Python, Node.js, exécution shell avec bac à sable Docker optionnel (`CODE_EXEC_BACKEND=docker`).
- **Pipeline RAG complet** — Intégration Jina + LanceDB + récupération hybride + reclassement + citations en ligne `[N]`.
- **Artefacts d'outils** — Les sorties enrichies (aperçus HTML, fichiers) sont rendues dans le chat.

#### Canaux de Messagerie (v0.8)
- **Pont IM étendu à l'organisation** — abstraction `BaseChannel` pour la messagerie sortante sur Slack, Microsoft Teams, Discord, Feishu (Lark), WeCom et DingTalk. La première implémentation livrée est Feishu ; Slack / Teams / WeCom / Email sont les prochains sur la feuille de route v0.9.
- **Identifiants chiffrés avec Fernet** — Les secrets d'application et les clés de chiffrement sont chiffrés au repos ; chaque signature de rappel entrant est vérifiée.
- **Cartes d'approbation interactives** — `GateHook` natif du canal (Feishu aujourd'hui, Slack/Teams ensuite) publie une carte Approuver / Rejeter dans votre groupe lorsqu'un appel d'outil sensible se déclenche ; l'outil se bloque jusqu'à ce qu'un membre du groupe appuie sur un verdict. Approbation avec intervention humaine sans moteur de flux de travail personnalisé.
- **Interface de navigation et sélection** — Pas besoin de copier les ID de canal bruts depuis la console du fournisseur ; le portail appelle l'API de la plateforme IM et affiche un sélecteur de groupe.

#### Plateforme
- **Multi-locataire** — Authentification JWT, isolation des organisations, panneau d'administration avec analytique d'utilisation et métriques des connecteurs.
- **Marketplace** — Publiez et abonnez-vous à des agents, connecteurs, bases de connaissances, compétences, workflows.
- **Compétences globales (POS)** — Procédures opérationnelles réutilisables chargées pour chaque utilisateur ; le mode progressif réduit les tokens d'environ 80 %.
- **6 langues** — EN, ZH, JA, KO, DE, FR. Les traductions sont [entièrement automatisées](https://docs.fim.ai/quickstart#internationalization).
- **Assistant de configuration à la première exécution**, thème sombre/clair, palette de commandes, SSE en continu, visualisation DAG.

> Approfondissement : [Architecture](https://docs.fim.ai/architecture/system-overview) · [Système de hooks](https://docs.fim.ai/architecture/hook-system) · [Canaux](https://docs.fim.ai/configuration/channels/overview) · [Modes d'exécution](https://docs.fim.ai/concepts/execution-modes) · [Pourquoi FIM One](https://docs.fim.ai/why) · [Paysage concurrentiel](https://docs.fim.ai/strategy/competitive-landscape)

## Architecture

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

Chaque connecteur et canal est un pont standardisé — l'agent ne sait pas et ne se soucie pas de savoir s'il communique avec SAP, un système de contrat personnalisé ou un groupe Feishu. Le système Hook exécute le code de la plateforme en dehors de la boucle LLM pour les approbations, les audits et les limites de débit ; les canaux transmettent les notifications sortantes et les cartes d'approbation aux plateformes de messagerie instantanée externes. Consultez [Architecture des connecteurs](https://docs.fim.ai/architecture/connector-architecture), [Système Hook](https://docs.fim.ai/architecture/hook-system) et [Canaux](https://docs.fim.ai/configuration/channels/overview) pour plus de détails.

## Configuration

FIM One fonctionne avec **n'importe quel fournisseur compatible OpenAI** :

| Fournisseur        | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | -------------------- |
| **OpenAI**         | `sk-...`      | *(par défaut)*                 | `gpt-4o`             |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`      |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6`  |
| **Ollama** (local) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`        |

Fichier `.env` minimal :

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default
# LLM_MODEL=gpt-4o                         # default
JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

> Référence complète : [Variables d'environnement](https://docs.fim.ai/configuration/environment-variables)

## Stack Technologique

| Couche      | Technologie                                                         |
| ----------- | ------------------------------------------------------------------- |
| Backend     | Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio                 |
| Frontend    | Next.js 14, React 18, Tailwind CSS, shadcn/ui, React Flow v12      |
| IA / RAG    | LLMs compatibles OpenAI, Jina AI (embed + search), LanceDB          |
| Base de données | SQLite (dev) / PostgreSQL (prod)                                    |
| Messagerie  | Abstraction `BaseChannel` (Slack, Teams, Discord, Feishu/Lark, WeCom, DingTalk), identifiants chiffrés Fernet, vérification de signature HMAC |
| Infrastructure | Docker, uv, pnpm, streaming SSE                                    |

## Développement

```bash
uv sync --all-extras          # install dependencies
pytest                         # run tests
pytest --cov=fim_one           # with coverage
ruff check src/ tests/         # lint
mypy src/                      # type check
bash scripts/setup-hooks.sh    # install git hooks (enables auto i18n)
```

## Feuille de route

Consultez la [Feuille de route](https://docs.fim.ai/roadmap) complète pour l'historique des versions et les fonctionnalités prévues.

## FAQ

Questions fréquemment posées sur le déploiement, les fournisseurs de LLM, la configuration système, et bien d'autres — consultez la [FAQ](https://docs.fim.ai/faq).

## Contribution

Nous accueillons les contributions de toutes sortes — code, documentation, traductions, rapports de bugs et idées.

> **Programme Pioneer** : Les 100 premiers contributeurs dont une PR est fusionnée sont reconnus comme **Contributeurs Fondateurs** avec des crédits permanents, un badge et un support prioritaire des problèmes. [En savoir plus &rarr;](CONTRIBUTING.md#-pioneer-program)

**Liens rapides :**

- [**Guide de Contribution**](CONTRIBUTING.md) — configuration, conventions, processus de PR
- [**Conventions de Développement**](https://docs.fim.ai/contributing) — sécurité des types, tests et normes de qualité du code
- [**Bons Premiers Problèmes**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — sélectionnés pour les nouveaux venus
- [**Problèmes Ouverts**](https://github.com/fim-ai/fim-one/issues) — bugs et demandes de fonctionnalités

**Sécurité :** Pour signaler une vulnérabilité, veuillez ouvrir un [problème GitHub](https://github.com/fim-ai/fim-one/issues) avec l'étiquette `[SECURITY]`. Pour les divulgations sensibles, contactez-nous via Discord DM.

## Historique des étoiles

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>

## Activité

![Alt](https://repobeats.axiom.co/api/embed/49402c7d85e343e9cb5909da7b48db1930c76554.svg "Repobeats analytics image")

## Contributeurs

Merci à ces personnes merveilleuses ([clé emoji](https://allcontributors.org/docs/en/emoji-key)) :

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/tao-hpu"><img src="https://avatars.githubusercontent.com/u/1250043?v=4?s=80" width="80px;" alt="Tao An"/><br /><sub><b>Tao An</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Code">💻</a> <a href="#maintenance-tao-hpu" title="Maintenance">🚧</a> <a href="#design-tao-hpu" title="Design">🎨</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tao-hpu" title="Documentation">📖</a> <a href="#projectManagement-tao-hpu" title="Project Management">📆</a> <a href="#ideas-tao-hpu" title="Ideas, Planning, & Feedback">🤔</a> <a href="#infra-tao-hpu" title="Infrastructure">🚇</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/tgonzalezc5"><img src="https://avatars.githubusercontent.com/u/102870299?v=4?s=80" width="80px;" alt="Teo Gonzalez Collazo"/><br /><sub><b>Teo Gonzalez Collazo</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Code">💻</a> <a href="https://github.com/fim-ai/fim-one/commits?author=tgonzalezc5" title="Tests">⚠️</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Houjiawei330"><img src="https://avatars.githubusercontent.com/u/49180524?v=4?s=80" width="80px;" alt="Houx."/><br /><sub><b>Houx.</b></sub></a><br /><a href="https://github.com/fim-ai/fim-one/commits?author=Houjiawei330" title="Code">💻</a> <a href="https://github.com/fim-ai/fim-one/issues?q=author%3AHoujiawei330" title="Bug reports">🐛</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

Ce projet suit la spécification [all-contributors](https://allcontributors.org/). Les contributions de toute nature sont les bienvenues !

## Licence

Licence FIM One Source Available. Il ne s'agit **pas** d'une licence open source approuvée par l'OSI.

**Autorisé** : utilisation interne, modification, distribution avec licence intacte, intégration dans des applications non concurrentes.

**Restreint** : SaaS multi-locataire, plateformes d'agents concurrentes, revente de marque blanche, suppression de la marque.

Pour les demandes de licence commerciale, veuillez ouvrir un problème sur [GitHub](https://github.com/fim-ai/fim-one).

Voir [LICENSE](LICENSE) pour les conditions complètes.

---

<div align="center">

🌐 [Site web](https://one.fim.ai/) · 📖 [Documentation](https://docs.fim.ai) · 📋 [Journal des modifications](https://docs.fim.ai/changelog) · 🐛 [Signaler un bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

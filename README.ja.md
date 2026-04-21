<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![CI](https://github.com/fim-ai/fim-one/actions/workflows/test.yml/badge.svg)](https://github.com/fim-ai/fim-one/actions/workflows/test.yml)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**あなたのシステムは相互に通信していません。FIM Oneはエンタープライズグレードの AI パワード ブリッジです — コパイロットとして組み込むか、ハブとしてすべてを接続します。**

🌐 [ウェブサイト](https://one.fim.ai/) · 📖 [ドキュメント](https://docs.fim.ai) · 📋 [変更履歴](https://docs.fim.ai/changelog) · 🐛 [バグ報告](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ セットアップをスキップ — FIM One をクラウドで試してください。**
> マネージド版は **[cloud.fim.ai](https://cloud.fim.ai/)** で利用可能です: Docker なし、API キーなし、設定なし。サインインして数秒でシステムの接続を開始できます。_アーリーアクセス、フィードバック歓迎。_

---

## 概要

すべての企業には相互に通信しないシステムがあります — ERP、CRM、OA、財務、HR、カスタムデータベース。FIM One は、既存のインフラストラクチャを変更することなく、これらすべてを接続する **AI駆動ハブ** です。

| モード           | 説明                                              | アクセス                  |
| -------------- | ------------------------------------------------------- | ----------------------- |
| **スタンドアロン** | 汎用AI アシスタント — 検索、コード、ナレッジベース         | ポータル                  |
| **コパイロット**    | ホストシステムのUIに組み込まれたAI                       | iframe / ウィジェット / 埋め込み |
| **ハブ**    | すべての接続されたシステム全体の中央AI オーケストレーション   | ポータル / API            |

```mermaid
graph LR
    ERP <--> Hub["🔗 FIM One Hub"]
    Database <--> Hub
    Lark <--> Hub
    Hub <--> CRM
    Hub <--> OA
    Hub <--> API[Custom API]
```

### スクリーンショット

**ダッシュボード** — 統計情報、アクティビティトレンド、トークン使用量、およびエージェントと会話への高速アクセス。

![Dashboard](./assets/screenshot-dashboard.png)

**エージェント チャット** — 接続されたデータベースに対する複数ステップのツール呼び出しを伴う ReAct 推論。

![Agent Chat](./assets/screenshot-agent-chat.png)

**DAG プランナー** — LLM生成の実行計画、並列ステップ、およびライブステータス追跡。

![DAG Planner](./assets/screenshot-dag-planner.png)

### デモ

**エージェントの使用**

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)

**プランナーモードの使用**

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)

## クイックスタート

### Docker（推奨）

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
# Edit .env: set LLM_API_KEY (and optionally LLM_BASE_URL, LLM_MODEL)

docker compose up --build -d
```

http://localhost:3000 を開いてください — 初回起動時に管理者アカウントを作成します。以上です。

```bash
docker compose up -d          # start
docker compose down           # stop
docker compose logs -f        # view logs
```

### ローカル開発

前提条件: Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+、pnpm。

```bash
git clone https://github.com/fim-ai/fim-one.git && cd fim-one

cp example.env .env           # Edit: set LLM_API_KEY

uv sync --all-extras
cd frontend && pnpm install && cd ..

./start.sh dev                # hot reload: Python --reload + Next.js HMR
```

| コマンド          | 起動内容                       | URL                            |
| ---------------- | --------------------------------- | ------------------------------ |
| `./start.sh`         | Next.js + FastAPI                 | localhost:3000 (UI) + :8000    |
| `./start.sh dev`     | 同上、ホットリロード付き             | 同上                           |
| `./start.sh dev:api` | API のみ、開発モード (ホットリロード)   | localhost:8000                 |
| `./start.sh dev:ui`  | フロントエンドのみ、開発モード (HMR)    | localhost:3000                 |
| `./start.sh api`     | FastAPI のみ (ヘッドレス)           | localhost:8000/api             |

> 本番環境へのデプロイ (Docker、リバースプロキシ、ゼロダウンタイム更新) については、[デプロイメントガイド](https://docs.fim.ai/quickstart#production-deployment)を参照してください。

## 主な機能

#### コネクタハブ
- **3つのデリバリーモード** — スタンドアロンアシスタント、組み込みコパイロット、または中央ハブ。同じエージェントコア。
- **あらゆるシステム、1つのパターン** — API、データベース、MCP サーバーを接続。アクションは認証注入を伴うエージェントツールとして自動登録。段階的開示メタツールにより、すべてのツールタイプ全体でトークン使用量を 80% 以上削減。
- **データベースコネクタ** — PostgreSQL、MySQL、Oracle、SQL Server、および中国のレガシーDB（DM、KingbaseES、GBase、Highgo）。スキーマ内観とAI駆動の注釈。
- **3つの構築方法** — OpenAPI仕様をインポート、AIチャットビルダー、またはMCPサーバーを直接接続。

#### 計画と実行
- **動的DAG計画** — LLMが実行時に目標を依存グラフに分解します。ハードコードされたワークフローはありません。
- **並行実行** — 独立したステップはasyncioを介して並列実行され、最大3ラウンドまで自動的に再計画します。
- **ReActエージェント** — 自動エラー回復を備えた構造化された推論と行動のループ。
- **エージェントハーネス** — 決定論的なガードレール用のHookミドルウェア、コンテキスト管理用のContextGuard、段階的開示メタツール、自己反省ループを備えた本番グレードの実行環境。
- **自動ルーティング** — クエリを分類し、最適なモード（ReActまたはDAG）にルーティングします。`AUTO_ROUTING`で設定可能。
- **拡張思考** — OpenAI o-series、Gemini 2.5+、Claudeのための思考の連鎖。

#### ワークフロー & ツール
- **ビジュアルワークフローエディタ** — 12ノードタイプ、ドラッグ&ドロップキャンバス (React Flow v12)、JSON形式でのインポート/エクスポート。
- **スマートファイルハンドリング** — アップロードされたファイルは自動的にコンテキストにインライン化（小規模）されるか、`read_uploaded_file`ツール経由でオンデマンド読み込み可能。インテリジェントドキュメント処理：PDF、DOCX、PPTXファイルはビジョン対応処理を取得し、モデルがビジョンをサポートする場合は埋め込み画像抽出を実行。スマートPDFモードはテキスト豊富なページからテキストを抽出し、スキャンされたページを画像としてレンダリング。
- **プラグイン可能なツール** — Python、Node.js、シェル実行（オプションのDockerサンドボックス `CODE_EXEC_BACKEND=docker`）。
- **完全なRAGパイプライン** — Jinaエンベディング + LanceDB + ハイブリッド検索 + リランカー + インライン `[N]` 引用。
- **ツールアーティファクト** — リッチ出力（HTMLプレビュー、ファイル）がチャット内でレンダリング。

#### プラットフォーム
- **マルチテナント** — JWT認証、組織の分離、使用分析とコネクタメトリクスを備えた管理パネル。
- **マーケットプレイス** — エージェント、コネクタ、ナレッジベース、スキル、ワークフローの公開と購読。
- **グローバルスキル（SOP）** — すべてのユーザーに対して読み込まれる再利用可能な運用手順。プログレッシブモードはトークンを約80%削減します。
- **6言語対応** — EN、ZH、JA、KO、DE、FR。翻訳は[完全に自動化](https://docs.fim.ai/quickstart#internationalization)されています。
- **初回セットアップウィザード**、ダーク/ライトテーマ、コマンドパレット、ストリーミングSSE、DAG可視化。

> 詳細情報: [アーキテクチャ](https://docs.fim.ai/architecture/system-overview) · [実行モード](https://docs.fim.ai/concepts/execution-modes) · [FIM Oneを選ぶ理由](https://docs.fim.ai/why) · [競争環境](https://docs.fim.ai/strategy/competitive-landscape)

## アーキテクチャ

```mermaid
graph TB
    subgraph app["Application Layer"]
        a["Portal · API · iframe · Lark/Slack Bot · Webhook · WeCom/DingTalk"]
    end
    subgraph mid["FIM One"]
        direction LR
        m1["Connectors<br/>+ MCP Hub"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems"]
        b["ERP · CRM · OA · Finance · Databases · Custom APIs"]
    end
    app --> mid --> biz
```

各コネクタは標準化されたブリッジです。エージェントは SAPと通信しているのか、カスタムデータベースと通信しているのかを知る必要も気にする必要もありません。詳細は[コネクタアーキテクチャ](https://docs.fim.ai/architecture/connector-architecture)を参照してください。

## 設定

FIM One は**任意の OpenAI 互換プロバイダー**で動作します:

| プロバイダー       | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | -------------------- |
| **OpenAI**         | `sk-...`      | *(デフォルト)*                 | `gpt-4o`             |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`      |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6`  |
| **Ollama** (ローカル) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`        |

最小限の `.env`:

```bash
LLM_API_KEY=sk-your-key
# LLM_BASE_URL=https://api.openai.com/v1   # default
# LLM_MODEL=gpt-4o                         # default
JINA_API_KEY=jina_...                       # unlocks web tools + RAG
```

> 完全なリファレンス: [環境変数](https://docs.fim.ai/configuration/environment-variables)

## テックスタック

| レイヤー       | テクノロジー                                                          |
| ----------- | ------------------------------------------------------------------- |
| バックエンド     | Python 3.11+, FastAPI, SQLAlchemy, Alembic, asyncio                 |
| フロントエンド    | Next.js 14, React 18, Tailwind CSS, shadcn/ui, React Flow v12      |
| AI / RAG    | OpenAI互換LLM, Jina AI (embed + search), LanceDB          |
| データベース    | SQLite (dev) / PostgreSQL (prod)                                    |
| インフラ       | Docker, uv, pnpm, SSEストリーミング                                    |

## 開発

```bash
uv sync --all-extras          # install dependencies
pytest                         # run tests
pytest --cov=fim_one           # with coverage
ruff check src/ tests/         # lint
mypy src/                      # type check
bash scripts/setup-hooks.sh    # install git hooks (enables auto i18n)
```

## ロードマップ

バージョン履歴と計画中の機能については、完全な[ロードマップ](https://docs.fim.ai/roadmap)を参照してください。

## FAQ

デプロイメント、LLMプロバイダー、システム要件など、よくある質問については、[FAQ](https://docs.fim.ai/faq)を参照してください。

## 貢献

あらゆる種類の貢献を歓迎します — コード、ドキュメント、翻訳、バグ報告、アイデア。

> **パイオニアプログラム**: PRがマージされた最初の100人の貢献者は、**創設貢献者**として認識され、永続的なクレジット、バッジ、優先的な問題サポートが付与されます。[詳細を見る &rarr;](CONTRIBUTING.md#-pioneer-program)

**クイックリンク:**

- [**貢献ガイド**](CONTRIBUTING.md) — セットアップ、規約、PRプロセス
- [**開発規約**](https://docs.fim.ai/contributing) — 型安全性、テスト、コード品質基準
- [**初心者向けの良い問題**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — 初心者向けに厳選
- [**オープンな問題**](https://github.com/fim-ai/fim-one/issues) — バグ & 機能リクエスト

**セキュリティ:** 脆弱性を報告する場合は、`[SECURITY]`タグを付けて[GitHubの問題](https://github.com/fim-ai/fim-one/issues)を開いてください。機密の報告については、Discord DMで私たちに連絡してください。

## Star History

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>

## アクティビティ

<p align="center">
  <a href="https://github.com/fim-ai/fim-one/pulse">
    <img src="https://github-readme-stats.vercel.app/api/pin/?username=fim-ai&repo=fim-one&show_owner=true&theme=dark" alt="fim-one repo stats" />
  </a>
</p>

<p align="center">
  <a href="https://github.com/fim-ai/fim-one/commits">
    <img src="https://github-readme-activity-graph.vercel.app/graph?username=fim-ai&theme=github-compact&hide_border=true&area=true&custom_title=fim-ai%20activity" alt="fim-ai activity graph" />
  </a>
</p>

## 貢献者

これらの素晴らしい人たちに感謝します（[絵文字キー](https://allcontributors.org/docs/en/emoji-key)）:

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

[![Contributors](https://contrib.rocks/image?repo=fim-ai/fim-one)](https://github.com/fim-ai/fim-one/graphs/contributors)

このプロジェクトは [all-contributors](https://allcontributors.org/) 仕様に従っています。あらゆる種類の貢献を歓迎します！

## ライセンス

FIM One Source Available License。これは**OSI認定のオープンソースライセンスではありません**。

**許可される用途**: 内部使用、修正、ライセンスを保持した配布、競合しないアプリケーションへの組み込み。

**制限される用途**: マルチテナント SaaS、競合するエージェントプラットフォーム、ホワイトラベル、ブランディングの削除。

商用ライセンスのお問い合わせは、[GitHub](https://github.com/fim-ai/fim-one) でイシューを開いてください。

詳細は [LICENSE](LICENSE) をご覧ください。

---

<div align="center">

🌐 [ウェブサイト](https://one.fim.ai/) · 📖 [ドキュメント](https://docs.fim.ai) · 📋 [変更履歴](https://docs.fim.ai/changelog) · 🐛 [バグ報告](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

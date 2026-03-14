<div align="center">

![FIM One Banner](./assets/banner.jpg)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-Source%20Available-orange)
[![GitHub stars](https://img.shields.io/github/stars/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/fim-ai/fim-one?style=social)](https://github.com/fim-ai/fim-one/network)
[![GitHub issues](https://img.shields.io/github/issues/fim-ai/fim-one)](https://github.com/fim-ai/fim-one/issues)
[![Follow on X](https://img.shields.io/twitter/follow/FIM_One?style=social)](https://x.com/FIM_One)
[![Discord](https://img.shields.io/discord/1480638265206771742?logo=discord&label=discord)](https://discord.gg/z64czxdC7z)
[![Product Hunt](https://img.shields.io/badge/Product%20Hunt-FIM%20One-DA552F?logo=producthunt&logoColor=white)](https://www.producthunt.com/products/fim-one)

[🌐 English](README.md) | [🇨🇳 中文](README.zh.md) | [🇯🇵 日本語](README.ja.md) | [🇰🇷 한국어](README.ko.md) | [🇩🇪 Deutsch](README.de.md) | [🇫🇷 Français](README.fr.md)

**あなたのシステムは相互に通信していません。FIM Oneはそれらすべてを AI に接続します — コード変更なし、データ移行なし。**

*AI駆動型コネクタハブ — 1つのシステムにコパイロットとして組み込むか、すべてをハブとして接続します。*

🌐 [ウェブサイト](https://one.fim.ai/) · 📖 [ドキュメント](https://docs.fim.ai) · 📋 [変更履歴](https://docs.fim.ai/changelog) · 🐛 [バグ報告](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>

> [!TIP]
> **☁️ セットアップをスキップ — FIM One をクラウドで試してください。**
> マネージド版は **[cloud.fim.ai](https://cloud.fim.ai/)** で利用可能です: Docker不要、APIキー不要、設定不要。サインインして、数秒でシステムの接続を開始できます。_早期アクセス、フィードバック歓迎。_

---

## 目次

- [概要](#overview)
- [ユースケース](#use-cases)
- [FIM Oneを選ぶ理由](#why-fim-one)
- [FIM Oneの位置付け](#where-fim-one-sits)
- [主な機能](#key-features)
- [アーキテクチャ](#architecture)
- [クイックスタート](#quick-start) (Docker / ローカル / 本番環境)
- [設定](#configuration)
- [開発](#development)
- [ロードマップ](#roadmap)
- [貢献](#contributing)
- [スター履歴](#star-history)
- [アクティビティ](#activity)
- [貢献者](#contributors)
- [ライセンス](#license)## 概要

すべての企業には相互に通信しないシステムがあります — ERP、CRM、OA、財務、HR、カスタムデータベース。各ベンダーのAIは独自の領域内では優れていますが、他のすべてのシステムについては盲目です。FIM Oneは、既存のインフラストラクチャを変更することなく、AIを通じてそれらすべてを接続する**外部の第三者ハブ**です。3つの配信モード、1つのエージェントコア：

| モード           | 説明                                                                       | アクセス方法                       |
| -------------- | -------------------------------------------------------------------------------- | --------------------------------------- |
| **スタンドアロン** | 汎用AIアシスタント — 検索、コード、ナレッジベース                      | ポータル                                  |
| **コパイロット**    | ホストシステムに組み込まれたAI — 既存のUIでユーザーと並行して動作        | iframe / ウィジェット / ホストページへの埋め込み |
| **ハブ**        | 中央AI オーケストレーション — すべてのシステムが接続され、クロスシステムインテリジェンス | ポータル / API                            |

```mermaid
graph LR
    ERP --> Hub["FIM One Hub<br/>(AI orchestration)"]
    Database --> Hub
    Lark --> Hub
    CRM --> Hub
    OA --> Hub
    API[Custom API] --> Hub
```

コアは常に同じです：ReAct推論ループ、動的DAG計画と並行実行、プラグイン可能なツール、ベンダーロックインがないプロトコルファーストアーキテクチャ。

### Agents の使用

![Using Agents](https://github.com/user-attachments/assets/b03d7750-eae6-4b16-9242-4c500d53d6cf)### Planner Mode の使用

![Using Planner Mode](https://github.com/user-attachments/assets/2b630496-2e62-4e14-bbdf-b8c707258390)## ユースケース

エンタープライズデータとワークフローはOA、ERP、財務、承認システムの内部に閉じ込められています。FIM Oneは、AIエージェントがこれらのシステムを読み書きできるようにします。既存のインフラストラクチャを変更することなく、システム間のプロセスを自動化します。

| シナリオ                  | 推奨される開始方法 | 自動化される内容                                                                                                |
| ------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| **法務・コンプライアンス**    | Copilot → Hub     | 契約条項の抽出、バージョン比較、ソース引用付きのリスクフラグ、OA承認の自動トリガー          |
| **IT運用**         | Hub               | アラート発火 → ログ取得 → 根本原因分析 → Lark/Slackへの修正配信 — 1つの完全なループ                 |
| **ビジネス運用**   | Copilot           | スケジュール済みデータサマリーをチームチャネルにプッシュ、ライブデータベースに対するアドホック自然言語クエリ         |
| **財務自動化**    | Hub               | 請求書検証、経費承認ルーティング、ERP会計システム間の台帳照合          |
| **調達**           | Copilot → Hub     | 要件 → ベンダー比較 → 契約ドラフト → 承認 — エージェントがシステム間のハンドオフを処理           |
| **開発者統合** | API               | OpenAPI仕様をインポートするか、チャットでAPIを説明 — コネクタが数分で作成され、エージェントツールとして自動登録 |## FIM One を選ぶ理由### Land and Expand

まず、**Copilot** を 1 つのシステム（例えば ERP）に組み込むことから始めます。ユーザーは使い慣れたインターフェース内で直接 AI と対話できます。財務データをクエリしたり、レポートを生成したり、ページを離れることなく回答を得たりできます。

価値が証明されたら、**Hub** をセットアップします。これはすべてのシステムを接続する中央ポータルです。ERP Copilot は組み込まれたまま実行され続け、Hub はシステム間のオーケストレーションを追加します。CRM で契約をクエリし、OA で承認をチェックし、Lark で関係者に通知する — すべて 1 つの場所から実行できます。

Copilot は 1 つのシステム内で価値を証明します。Hub はすべてのシステム全体で価値を解き放ちます。### FIM Oneが行わないこと

FIM Oneは、ターゲットシステムに既に存在するワークフロー ロジックを複製しません：

- **BPM/FSMエンジンなし** — 承認チェーン、ルーティング、エスカレーション、ステートマシンはターゲットシステムの責任です。これらのシステムはこのロジックの構築に何年も費やしています。
- **BPM/FSMワークフローエンジンなし** — FIM OneのWorkflow Blueprintsは自動化テンプレート（LLM呼び出し、条件分岐、コネクタアクション）であり、ビジネスプロセス管理ではありません。承認チェーン、ルーティングルール、ステートマシンはターゲットシステムに属します。
- **コネクタ = APIコール** — コネクタの観点からは、「承認を転送」= 1つのAPIコール、「理由付きで却下」= 1つのAPIコールです。すべての複雑なワークフロー操作はHTTPリクエストに集約されます。FIM OneはAPIを呼び出し、ターゲットシステムが状態を管理します。

これは機能ギャップではなく、意図的なアーキテクチャ上の境界です。

### 競争的ポジショニング

|                        | Dify                       | Manus            | Coze                  | FIM One                      |
| ---------------------- | -------------------------- | ---------------- | --------------------- | ---------------------------- |
| **Approach**           | ビジュアルワークフロービルダー    | 自律エージェント | ビルダー + エージェントスペース | AI Connector Hub             |
| **Planning**           | 人間が設計した静的DAG | マルチエージェントCoT  | 静的 + 動的      | LLM DAG planning + ReAct     |
| **Cross-system**       | APIノード（手動）         | なし               | プラグインマーケットプレイス    | Hub Mode（N:N オーケストレーション） |
| **Human Confirmation** | なし                         | なし               | なし                    | あり（実行前ゲート）     |
| **Self-hosted**        | あり（Dockerスタック）         | なし               | あり（Coze Studio）     | あり（シングルプロセス）         |

> 詳細情報：[Philosophy](https://docs.fim.ai/architecture/philosophy) | [Execution Modes](https://docs.fim.ai/concepts/execution-modes) | [Competitive Landscape](https://docs.fim.ai/strategy/competitive-landscape)### FIM One の位置付け

```
                Static Execution          Dynamic Execution
            ┌──────────────────────┬──────────────────────┐
 Static     │ BPM / Workflow       │ ACM                  │
 Planning   │ Camunda, Activiti    │ (Salesforce Case)    │
            │ Dify, n8n, Coze     │                      │
            ├──────────────────────┼──────────────────────┤
 Dynamic    │ (transitional —      │ Autonomous Agent     │
 Planning   │  unstable quadrant)  │ AutoGPT, Manus       │
            │                      │ ★ FIM One (bounded)│
            └──────────────────────┴──────────────────────┘
```

Dify/n8n は **Static Planning + Static Execution** です — ユーザーがビジュアルキャンバス上で DAG を設計し、ノードが固定操作を実行します。FIM One は **Dynamic Planning + Dynamic Execution** です — LLM が実行時に DAG を生成し、各ノードが ReAct ループを実行し、目標が達成されない場合は再計画します。ただし制限があります（最大 3 回の再計画ラウンド、トークン予算、確認ゲート）ため、AutoGPT よりも制御されています。

FIM One は BPM/FSM を行いません — ワークフロー ロジックはターゲット システムに属し、Connector は API を呼び出すだけです。

> 詳細な説明: [Philosophy](https://docs.fim.ai/architecture/philosophy)## 主な機能#### コネクタープラットフォーム（コア）
- **Connector Hub アーキテクチャ** — スタンドアロンアシスタント、組み込み Copilot、または中央 Hub — 同じエージェントコア、異なるデリバリー。
- **任意のシステム、1つのパターン** — API、データベース、メッセージバスを接続。アクションは認証注入（Bearer、API Key、Basic）を伴うエージェントツールとして自動登録されます。
- **データベースコネクタ** — PostgreSQL、MySQL、Oracle、SQL Server、および中国のレガシーデータベース（DM、KingbaseES、GBase、Highgo）への直接 SQL アクセス。スキーマ内省、AI 搭載の注釈、読み取り専用クエリ実行、および保存時の暗号化された認証情報。各 DB コネクタは 3 つのツール（`list_tables`、`describe_table`、`query`）を自動生成します。
- **コネクタを構築する 3 つの方法：**
  - *OpenAPI スペックをインポート* — YAML/JSON/URL をアップロード。コネクタとすべてのアクションが自動生成されます。
  - *AI チャットビルダー* — API を自然言語で説明。AI がアクション設定を会話内で生成および反復します。10 個の特化したビルダーツールがコネクタ設定、アクション、テスト、およびエージェント配線を処理します。
  - *MCP エコシステム* — 任意の MCP サーバーを直接接続。サードパーティの MCP コミュニティがそのまま機能します。#### インテリジェント計画と実行
- **Dynamic DAG Planning** — LLM は実行時に目標を依存グラフに分解します。ハードコードされたワークフローはありません。
- **並行実行** — 独立したステップは asyncio を介して並列実行されます。
- **DAG 再計画** — 目標が達成されない場合、最大 3 ラウンドまで自動的に計画を修正します。
- **ReAct Agent** — 自動エラー回復を備えた構造化された推論と行動のループ。
- **自動ルーティング** — 各リクエストを最適な実行モード（ReAct または DAG）に自動的に分類します。フロントエンドは 3 方向トグル（Auto/Standard/Planner）をサポートしています。`AUTO_ROUTING` で設定可能です。
- **拡張思考** — サポートされているモデル（OpenAI o-series、Gemini 2.5+、Claude）に対して `LLM_REASONING_EFFORT` を介してチェーン・オブ・ソート推論を有効にします。モデルの推論は UI の「thinking」ステップで表示されます。#### ワークフロー ブループリント
- **ビジュアル ワークフロー エディター** — React Flow v12 上に構築されたドラッグ アンド ドロップ キャンバスを使用して、マルチステップ オートメーション ブループリントを設計します。12 のノード タイプ: Start、End、LLM、Condition Branch、Question Classifier、エージェント、Knowledge Retrieval、コネクタ、HTTP Request、Variable Assign、Template Transform、Code Execution。
- **トポロジカル実行エンジン** — ワークフローは依存関係の順序でノードを実行し、条件分岐、ノード間変数の受け渡し、およびリアルタイム SSE ステータス ストリーミングをサポートします。
- **インポート/エクスポート** — ワークフロー ブループリントを JSON として共有します。安全な認証情報処理のための暗号化された環境変数。

#### ツール & インテグレーション
- **プラグイン可能なツールシステム** — 自動検出; Python executor、Node.js executor、計算機、ウェブ検索/フェッチ、HTTP リクエスト、シェル実行など多数を搭載。
- **プラグイン可能なサンドボックス** — `python_exec` / `node_exec` / `shell_exec` はローカルまたは Docker モード (`CODE_EXEC_BACKEND=docker`) で実行され、OS レベルの分離 (`--network=none`, `--memory=256m`) を実現。SaaS およびマルチテナント環境に対応。
- **MCP プロトコル** — 任意の MCP サーバーをツールとして接続。サードパーティ MCP エコシステムがそのまま動作。
- **ツールアーティファクトシステム** — ツールはリッチな出力 (HTML プレビュー、生成ファイル) を生成し、チャット内でのレンダリングとダウンロードに対応。HTML アーティファクトはサンドボックス化された iframe でレンダリング; ファイルアーティファクトはダウンロードチップを表示。
- **OpenAI 互換** — 任意の `/v1/chat/completions` プロバイダー (OpenAI、DeepSeek、Qwen、Ollama、vLLM…) に対応。#### RAG & Knowledge
- **Full RAG Pipeline** — Jina embedding + LanceDB + FTS + RRF hybrid retrieval + reranker。PDF、DOCX、Markdown、HTML、CSVをサポート。
- **Grounded Generation** — エビデンスアンカー型RAGでインライン`[N]`引用、競合検出、および説明可能な信頼度スコア。
- **KB Document Management** — チャンクレベルのCRUD、チャンク全体のテキスト検索、失敗したドキュメントの再試行、およびベクトルストアスキーマの自動マイグレーション。#### Portal & UX
- **Real-time Streaming (SSE v2)** — Split event protocol (`done` / `suggestions` / `title` / `end`) with streaming dot-pulse cursor, KaTeX math rendering, and tool step folding.
- **DAG Visualization** — Interactive flow graph with live status, dependency edges, click-to-scroll, and re-plan round snapshots as collapsible cards.
- **Conversational Interrupt** — Send follow-up messages while the agent is running; injected at the next iteration boundary.
- **Dark / Light / System Theme** — Full theme support with system-preference detection.
- **Command Palette** — Conversation search, starring, batch operations, and title rename.#### プラットフォーム & マルチテナント
- **JWT Auth** — トークンベースの SSE 認証、会話の所有権、ユーザーごとのリソース分離。
- **エージェント管理** — バインドされたモデル、ツール、指示を持つエージェントを作成、構成、公開します。エージェントごとの実行モード（Standard/Planner）と温度制御。オプションの `discoverable` フラグにより、CallAgentTool 経由での LLM 自動検出が有効になります。
- **エージェント スキル システム** — エージェントの段階的な指示読み込み。スキル（SOP、スクリプト、ドメイン知識）はシステムプロンプトで軽量スタブ（各約 15 トークン）として参照され、エージェントは `read_skill(name)` を呼び出して必要に応じて完全なコンテンツを読み込み、会話ごとの指示トークンコストを約 80% 削減します。カスタム ContextGuard 圧縮戦略用のエージェントごとの `compact_instructions` フィールド。
- **マーケットプレイス（Shadow Market Org）** — 組み込みの Market org はリソース共有用の見えないバックエンドエンティティとして動作します。リソースはマーケットプレイスの閲覧を通じて検出され、明示的にサブスクライブされます（プルモデル）— 自動参加メンバーシップはありません。マーケットプレイスへの公開には常にレビューが必要です。
- **リソース サブスクリプション** — ユーザーはマーケットプレイスから共有リソースを閲覧してサブスクライブします。UI または API 経由でサブスクライブ/アンサブスクライブします。すべてのリソースタイプ（エージェント、コネクタ、ナレッジベース、MCP サーバー、スキル、ワークフロー）がマーケットプレイス公開とサブスクリプション管理をサポートします。
- **管理パネル** — システム統計ダッシュボード（ユーザー、会話、トークン、モデル使用状況チャート、エージェント別トークン内訳）、コネクタ呼び出しメトリクス（成功率、レイテンシ、呼び出し数）、検索/ページネーション付きユーザー管理、ロール切り替え、パスワードリセット、アカウント有効化/無効化、ツールごとの有効化/無効化制御。
- **初回セットアップ ウィザード** — 初回起動時、ポータルは管理者アカウント（ユーザー名、パスワード、メール）の作成をガイドします。この 1 回限りのセットアップがログイン認証情報になります — 設定ファイルは不要です。
- **個人センター** — ユーザーごとのグローバルシステム指示。すべての会話に適用されます。
- **言語設定** — ユーザーごとの言語設定（auto/en/zh）。すべての LLM 応答を選択した言語に指示します。

#### コンテキスト & メモリ
- **LLM Compact** — トークン予算内に収まるようにするための自動 LLM 駆動の要約。
- **ContextGuard + Pinned Messages** — トークン予算マネージャー。ピン留めされたメッセージはコンパクション から保護されます。
- **デュアルデータベースサポート** — SQLite（ゼロコンフィグのデフォルト）で数秒で開始できます。PostgreSQL は本番環境とマルチワーカーデプロイメント向けです。Docker Compose は PostgreSQL をヘルスチェック付きで自動プロビジョニングします。`docker compose up` で稼働します。## アーキテクチャ### システム概要

```mermaid
graph TB
    subgraph app["Application & Interaction Layer"]
        a["Portal · API · iframe · Lark/Slack Bot · Webhook · WeCom/DingTalk"]
    end
    subgraph mid["FIM One Middleware"]
        direction LR
        m1["Connectors<br/>+ MCP Hub"] ~~~ m2["Orch Engine<br/>ReAct / DAG"] ~~~ m3["RAG /<br/>Knowledge"] ~~~ m4["Auth /<br/>Admin"]
    end
    subgraph biz["Business Systems & Data Layer"]
        b["ERP · CRM · OA · Finance · Databases · Custom APIs<br/>Lark · DingTalk · WeCom · Slack · Email · Webhook"]
    end
    app --> mid --> biz
```### Connector Hub

```mermaid
graph LR
    ERP["ERP<br/>(SAP/Kingdee)"] --> A
    CRM["CRM<br/>(Salesforce)"] --> B
    OA["OA<br/>(Seeyon/Weaver)"] --> C
    DB["Custom DB<br/>(PG/MySQL)"] --> D
    subgraph Hub["FIM One Hub"]
        A["Agent A: Finance Audit"]
        B["Agent B: Contract Review"]
        C["Agent C: Approval Assist"]
        D["Agent D: Data Reporting"]
    end
    A --> O1["Lark / Slack"]
    B --> O2["Email / WeCom"]
    C --> O3["Teams / Webhook"]
    D --> O4["Any API"]
```

*Portal / API / iframe*

各 connector は標準化されたブリッジです。agent は SAP と通信しているのか、カスタム PostgreSQL データベースと通信しているのかを知る必要も気にする必要もありません。詳細については、[Connector Architecture](https://docs.fim.ai/architecture/connector-architecture) を参照してください。### 内部実行

FIM One は2つの実行モードを提供し、それらの間で自動的にルーティングします:

| モード         | 最適な用途                | 動作方法                                                       |
| ------------ | ------------------------- | ------------------------------------------------------------------ |
| Auto         | すべてのクエリ (デフォルト)     | 高速 LLM がクエリを分類し、ReAct または DAG にルーティング           |
| ReAct        | 単一の複雑なクエリ    | Reason → Act → Observe ループ (ツール付き)                             |
| DAG Planning | マルチステップの並列タスク | LLM が依存グラフを生成し、独立したステップが並行実行 |

```mermaid
graph TB
    Q[User Query] --> P["DAG Planner<br/>LLM decomposes the goal into steps + dependency edges"]
    P --> E["DAG Executor<br/>Launches independent steps concurrently via asyncio<br/>Each step is handled by a ReAct Agent"]
    E --> R1["ReAct Agent 1 → Tools<br/>(python_exec, custom, ...)"]
    E --> R2["ReAct Agent 2 → RAG<br/>(retriever interface)"]
    E --> RN["ReAct Agent N → ..."]
    R1 & R2 & RN --> An["Plan Analyzer<br/>LLM evaluates results · re-plans if goal not met"]
    An --> F[Final Answer]
```## クイックスタート### Option A: Docker (推奨)

ローカルの Python や Node.js は不要です — すべてコンテナ内でビルドされます。

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one
```# 設定 — LLM_API_KEY のみが必須です
cp example.env .env# .env を編集: LLM_API_KEY を設定 (オプションで LLM_BASE_URL、LLM_MODEL も設定)# ビルドと実行（初回、または新しいコードをプルした後）
```bash
docker compose up --build -d
```

http://localhost:3000 を開く — 初回起動時は、管理者アカウントの作成ガイドが表示されます。以上です。

初回ビルド後、その後の起動には以下のみが必要です：

```bash
docker compose up -d          # 起動（イメージが変更されていない場合はリビルドをスキップ）
docker compose down           # 停止
docker compose logs -f        # ログを表示
```

データは Docker 名前付きボリューム（`fim-data`、`fim-uploads`）に永続化され、コンテナの再起動後も保持されます。

> **注意：** Docker モードはホットリロードをサポートしていません。コード変更にはイメージの再ビルド（`docker compose up --build -d`）が必要です。ライブリロード付きのアクティブな開発には、以下の**オプション B** を使用してください。### オプション B: ローカル開発

前提条件: Python 3.11+、[uv](https://docs.astral.sh/uv/)、Node.js 18+、pnpm。

```bash
git clone https://github.com/fim-ai/fim-one.git
cd fim-one

cp example.env .env
```# .env を編集: LLM_API_KEY を設定# インストール
uv sync --all-extras
cd frontend && pnpm install && cd ..# 起動（ホットリロード付き）
./start.sh dev
```

| コマンド          | 起動内容                                             | URL                                      |
| ---------------- | ------------------------------------------------------- | ---------------------------------------- |
| `./start.sh`     | Next.js + FastAPI                                       | http://localhost:3000 (UI) + :8000 (API) |
| `./start.sh dev` | 同じ、ホットリロード付き（Python `--reload` + Next.js HMR） | 同じ                                     |
| `./start.sh api` | FastAPI のみ（ヘッドレス、統合またはテスト用）     | http://localhost:8000/api                |### 本番環境へのデプロイ

どちらの方法でも本番環境で動作します:

| 方法     | コマンド                | 最適な用途                                    |
| ---------- | ---------------------- | ------------------------------------------- |
| **Docker** | `docker compose up -d` | ハンズオフデプロイ、簡単なアップデート          |
| **スクリプト** | `./start.sh`           | ベアメタルサーバー、カスタムプロセスマネージャー |

どちらの方法でも、HTTPS とカスタムドメイン用に Nginx リバースプロキシをフロントに配置してください:

```
User → Nginx (443/HTTPS) → localhost:3000
```

API は内部的にポート 8000 で実行されます — Next.js は `/api/*` リクエストを自動的にプロキシします。ポート 3000 のみを公開する必要があります。

**実行中のデプロイメントを更新する** (ダウンタイムなし):

```bash
cd /path/to/fim-one \
  && git pull origin master \
  && sudo docker compose build \
  && sudo docker compose up -d \
  && sudo docker image prune -f
```

`build` は古いコンテナがトラフィックを処理し続けている間に最初に実行されます。その後 `up -d` はイメージが変更されたコンテナのみを置き換えます — ダウンタイムは数分ではなく約 10 秒です。

コード実行サンドボックス (`CODE_EXEC_BACKEND=docker`) を使用する場合は、Docker ソケットをマウントしてください:

```yaml
# docker-compose.yml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```## 設定### 推奨セットアップ

FIM One は **任意の OpenAI 互換 LLM プロバイダー** で動作します — OpenAI、DeepSeek、Anthropic、Qwen、Ollama、vLLM など。お好みのものを選択してください：

| プロバイダー       | `LLM_API_KEY` | `LLM_BASE_URL`                 | `LLM_MODEL`         |
| ------------------ | ------------- | ------------------------------ | ------------------- |
| **OpenAI**         | `sk-...`      | *(デフォルト)*                 | `gpt-4o`            |
| **DeepSeek**       | `sk-...`      | `https://api.deepseek.com/v1`  | `deepseek-chat`     |
| **Anthropic**      | `sk-ant-...`  | `https://api.anthropic.com/v1` | `claude-sonnet-4-6` |
| **Ollama** (ローカル) | `ollama`      | `http://localhost:11434/v1`    | `qwen2.5:14b`       |

**[Jina AI](https://jina.ai/)** は Web 検索/取得、埋め込み、完全な RAG パイプラインを実現します（無料ティア利用可能）。

最小限の `.env`：

```bash
LLM_API_KEY=sk-your-key
```# LLM_BASE_URL=https://api.openai.com/v1   # デフォルト — 他のプロバイダーの場合は変更してください# LLM_MODEL=gpt-4o                         # デフォルト — 他のモデルに変更可能

JINA_API_KEY=jina_...                       # ウェブツール + RAG を有効化
```### すべての変数

すべての設定オプション（LLM、エージェント実行、ウェブツール、RAG、コード実行、画像生成、コネクタ、プラットフォーム、OAuth）については、完全な[環境変数](https://docs.fim.ai/configuration/environment-variables)リファレンスを参照してください。## 開発

```bash


```# すべての依存関係をインストール（dev extrasを含む）
uv sync --all-extras# テストを実行する
pytest# カバレッジ付きでテストを実行
pytest --cov=fim_one --cov-report=term-missing# Lint
ruff check src/ tests/# 型チェック
mypy src/# Git フックをインストール (クローン後に 1 回実行 — コミット時に自動 i18n 翻訳を有効化)
bash scripts/setup-hooks.sh
```## 国際化 (i18n)

FIM One は **6 言語** をサポートしています: 英語、中国語、日本語、韓国語、ドイツ語、フランス語。翻訳は完全に自動化されており、英語のソースファイルを編集するだけで済みます。

**サポートされている言語**: `en` `zh` `ja` `ko` `de` `fr`

| 対象 | ソース (これを編集) | 自動生成 (編集しないこと) |
|------|--------------------|-----------------------------|
| UI 文字列 | `frontend/messages/en/*.json` | `frontend/messages/{locale}/*.json` |
| ドキュメント | `docs/*.mdx` | `docs/{locale}/*.mdx` |
| README | `README.md` | `README.{locale}.md` |

**仕組み**: プリコミットフックが英語ファイルへの変更を検出し、プロジェクトの Fast LLM を使用して翻訳します。翻訳は段階的に行われます — 新規、変更、または削除されたコンテンツのみが処理されます。

```bash# セットアップ（クローン後に1回実行）
bash scripts/setup-hooks.sh# 完全な翻訳（初回またはロケールを追加した後）
uv run scripts/translate.py --all# 特定のファイルを翻訳する
uv run scripts/translate.py --files frontend/messages/en/common.json# ターゲットロケールをオーバーライドする
uv run scripts/translate.py --all --locale ja ko# API呼び出しの並列化を増やす（デフォルト: 3、APIが許可する場合は増加）
uv run scripts/translate.py --all --concurrency 10# 日常のワークフロー: コミットするだけ — フックが自動的にすべてを処理
git add frontend/messages/en/common.json
git commit -m "feat(i18n): add new strings"  # hook auto-translates
```

| フラグ | デフォルト | 説明 |
|------|---------|-------------|
| `--all` | — | すべてを再翻訳（キャッシュを無視） |
| `--files` | — | 特定のファイルのみを翻訳 |
| `--locale` | auto-discover | ターゲットロケールをオーバーライド |
| `--concurrency` | 3 | 最大並列 LLM API 呼び出し数 |
| `--force` | — | すべての JSON キーの再翻訳を強制 |

**新しい言語を追加する**: `mkdir frontend/messages/{locale}` → `--all` を実行 → `frontend/src/i18n/request.ts` の `SUPPORTED_LOCALES` にロケールを追加。## ロードマップ

完全な[ロードマップ](https://docs.fim.ai/roadmap)でバージョン履歴と今後の予定を確認してください。## 貢献

あらゆる種類の貢献を歓迎します — コード、ドキュメント、翻訳、バグレポート、アイデアなど。

> **Pioneer Program**: 最初の100人のコントリビューターでPRがマージされた方は、**Founding Contributors**として認識され、プロジェクトの永続的なクレジット、プロフィールバッジ、優先的な問題サポートが得られます。[詳細はこちら &rarr;](CONTRIBUTING.md#-pioneer-program)

**クイックリンク:**

- [**Contributing Guide**](CONTRIBUTING.md) — セットアップ、規約、PRプロセス
- [**Good First Issues**](https://github.com/fim-ai/fim-one/labels/good%20first%20issue) — 初心者向けに厳選
- [**Open Issues**](https://github.com/fim-ai/fim-one/issues) — バグ & 機能リクエスト## スター履歴

<a href="https://star-history.com/#fim-ai/fim-one&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=fim-ai/fim-one&type=Date" />
  </picture>
</a>## Activity

![Alt](https://repobeats.axiom.co/api/embed/ff8b449fd183a323d81f33fc96e7ce42ad745f65.svg "Repobeats analytics image")## 貢献者

これらの素晴らしい人々に感謝します（[絵文字キー](https://allcontributors.org/docs/en/emoji-key)）:

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

[![Contributors](https://contrib.rocks/image?repo=fim-ai/fim-one)](https://github.com/fim-ai/fim-one/graphs/contributors)

このプロジェクトは [all-contributors](https://allcontributors.org/) 仕様に従っています。あらゆる種類の貢献を歓迎します！## ライセンス

FIM One Source Available License。これは**OSI認可のオープンソースライセンスではありません**。

**許可される用途**: 内部使用、修正、ライセンスを保持したままでの配布、独自の（競合しない）アプリケーションへの組み込み。

**制限される用途**: マルチテナント SaaS、競合するエージェントプラットフォーム、ホワイトラベル、ブランディングの削除。

商用ライセンスのお問い合わせについては、[GitHub](https://github.com/fim-ai/fim-one) でイシューを開いてください。

詳細は [LICENSE](LICENSE) をご覧ください。

---

<div align="center">

🌐 [Website](https://one.fim.ai/) · 📖 [Docs](https://docs.fim.ai) · 📋 [Changelog](https://docs.fim.ai/changelog) · 🐛 [Report Bug](https://github.com/fim-ai/fim-one/issues) · 💬 [Discord](https://discord.gg/z64czxdC7z) · 🐦 [Twitter](https://x.com/FIM_One) · 🏆 [Product Hunt](https://www.producthunt.com/products/fim-one)

</div>